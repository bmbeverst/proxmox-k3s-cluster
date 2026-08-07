"""These are the apps that are setup to run the cluster infrastructure"""

from typing import Optional
import os

import pulumi
import pulumi_kubernetes as k8s
import yaml

from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.yaml.v2 import ConfigFile

from pulumi import ResourceOptions


SUC_VERSION = "v0.20.1"

# Chart repos/versions live in Chart.yaml so Renovate's helmv3 manager can bump them.
with open(os.path.join(os.path.dirname(__file__), "Chart.yaml")) as f:
    _chart_deps = {d["name"]: d for d in yaml.safe_load(f)["dependencies"]}


suc_crd = k8s.yaml.v2.ConfigFile(
    "system-upgrade-controller-crd",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/crd.yaml",
)

suc = k8s.yaml.v2.ConfigFile(
    "system-upgrade-controller",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/system-upgrade-controller.yaml",
    opts=pulumi.ResourceOptions(
        depends_on=[suc_crd],
    ),
)

server_plan = CustomResource(
    "server-plan",
    api_version="upgrade.cattle.io/v1",
    kind="Plan",
    metadata={
        "name": "server-plan",
        "namespace": "system-upgrade",
    },
    spec={
        "concurrency": 1,
        "cordon": True,
        # Drain the pods off the node before restart k3s
        # cordon is automatically handled
        "drain": {
            # Delete manually started pods instead of
            # failing the drain. Default --ignore-daemonsets and
            # --delete-emptydir-data
            "force": True,
            # PodDisruptionBudget can refuse evictions
            # causing the upgrade to hang, directly delete pods
            # but will kill after terminationGracePeriodSeconds
            "disableEviction": True,
            "skipWaitForDeleteTimeout": 60,
        },
        "nodeSelector": {
            "matchExpressions": [
                {
                    "key": "node-role.kubernetes.io/control-plane",
                    "operator": "In",
                    "values": ["true"],
                }
            ],
        },
        "serviceAccountName": "system-upgrade",
        "upgrade": {
            "image": "rancher/k3s-upgrade",
        },
        "channel": "https://update.k3s.io/v1-release/channels/stable",
    },
    opts=ResourceOptions(depends_on=[suc]),
)

agent_plan = CustomResource(
    "agent-plan",
    api_version="upgrade.cattle.io/v1",
    kind="Plan",
    metadata={
        "name": "agent-plan",
        "namespace": "system-upgrade",
    },
    spec={
        # This is the same plan as the server-plan
        # but with nodeSelector updated to exclude control-plane nodes.
        # Currently no worker nodes :(
        "concurrency": 1,
        "cordon": True,
        "drain": {
            "force": True,
            "disableEviction": True,
            "skipWaitForDeleteTimeout": 60,
        },
        "nodeSelector": {
            "matchExpressions": [
                {
                    "key": "node-role.kubernetes.io/control-plane",
                    "operator": "DoesNotExist",
                }
            ],
        },
        "serviceAccountName": "system-upgrade",
        "prepare": {
            "image": "rancher/k3s-upgrade",
            "args": ["prepare", "server-plan"],
        },
        "upgrade": {
            "image": "rancher/k3s-upgrade",
        },
        "channel": "https://update.k3s.io/v1-release/channels/stable",
    },
    opts=ResourceOptions(depends_on=[server_plan]),
)

# Add the flatcar-specific SSL paths
suc_patch = k8s.apps.v1.DeploymentPatch(
    "system-upgrade-controller-patch",
    metadata=k8s.meta.v1.ObjectMetaPatchArgs(
        name="system-upgrade-controller",
        namespace="system-upgrade",
    ),
    spec=k8s.apps.v1.DeploymentSpecPatchArgs(
        template=k8s.core.v1.PodTemplateSpecPatchArgs(
            spec=k8s.core.v1.PodSpecPatchArgs(
                containers=[
                    k8s.core.v1.ContainerPatchArgs(
                        name="system-upgrade-controller",
                        volume_mounts=[
                            k8s.core.v1.VolumeMountPatchArgs(
                                mount_path="/usr/share/ssl",
                                name="usr-share-ssl",
                                read_only=True,
                            ),
                            k8s.core.v1.VolumeMountPatchArgs(
                                mount_path="/usr/share/ca-certificates",
                                name="usr-share-ca-certificates",
                                read_only=True,
                            ),
                        ],
                    )
                ],
                volumes=[
                    k8s.core.v1.VolumePatchArgs(
                        name="usr-share-ssl",
                        host_path=k8s.core.v1.HostPathVolumeSourcePatchArgs(
                            path="/usr/share/ssl",
                            type="DirectoryOrCreate",
                        ),
                    ),
                    k8s.core.v1.VolumePatchArgs(
                        name="usr-share-ca-certificates",
                        host_path=k8s.core.v1.HostPathVolumeSourcePatchArgs(
                            path="/usr/share/ca-certificates",
                            type="DirectoryOrCreate",
                        ),
                    ),
                ],
            )
        )
    ),
    opts=pulumi.ResourceOptions(depends_on=[suc]),
)


# Kured reboots nodes when update-engine touches /run/reboot-required
# chart defaults handle the sentinel path and control-plane taints.
kured = k8s.helm.v3.Release(
    "kured",
    k8s.helm.v3.ReleaseArgs(
        chart="kured",
        version=_chart_deps["kured"]["version"],
        namespace="kured",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["kured"]["repository"],
        ),
        values={
            "configuration": {
                # One node at a time: losing two of three control-plane nodes takes down etcd
                "concurrency": 1,
                # Abort drain after 10m, kured retries.
                "drainTimeout": "10m",
                "skipWaitForDeleteTimeout": 60,
            },
        },
        timeout=600,
    ),
)


# kube-vip HA control plane on 10.10.1.99
kube_vip = k8s.helm.v3.Release(
    "kube-vip",
    k8s.helm.v3.ReleaseArgs(
        chart="kube-vip",
        version=_chart_deps["kube-vip"]["version"],
        namespace="kube-system",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["kube-vip"]["repository"],
        ),
        values={
            "config": {"address": "10.10.1.99"},
            "env": {
                "vip_interface": "eth0",
                "vip_arp": "true",
                "vip_subnet": "32",
                "cp_enable": "true",
                "svc_enable": "false",
                "vip_leaderelection": "true",
                "lb_enable": "false",
                "vip_leaseduration": "5",
                "vip_renewdeadline": "3",
                "vip_retryperiod": "1",
            },
            "envValueFrom": {
                "vip_nodename": {"fieldRef": {"fieldPath": "spec.nodeName"}},
            },
            "resources": {
                "requests": {"cpu": "10m", "memory": "32Mi"},
                "limits": {"cpu": "100m", "memory": "64Mi"},
            },
        },
        timeout=600,
    ),
)
