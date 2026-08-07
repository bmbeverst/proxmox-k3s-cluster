"""These are the apps that are setup to run the cluster infrastructure"""

from typing import Optional
import pulumi
import pulumi_kubernetes as k8s

from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.yaml.v2 import ConfigFile

from pulumi import ResourceOptions


SUC_VERSION = "v0.19.2"
KURED_CHART_VERSION = "6.1.0"  # appVersion: kured 1.23.0
KUBE_VIP_CHART_VERSION = "0.11.0"  # appVersion: kube-vip v1.2.2


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
        # Drain the pods off the node before the upgrade job restarts k3s on
        # the host. When drain is specified the node is cordoned automatically
        # and uncordoned once the upgrade completes successfully.
        "drain": {
            # Delete bare pods not managed by a controller instead of
            # failing the drain. The controller already passes
            # --ignore-daemonsets and --delete-emptydir-data by default.
            "force": True,
            # On a 3-node cluster a PodDisruptionBudget can refuse evictions
            # forever and hang the upgrade, so delete pods directly (still a
            # graceful delete honoring terminationGracePeriodSeconds) and
            # stop waiting on pods stuck terminating.
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
        "concurrency": 1,
        "cordon": True,
        # Drain the pods off the node before the upgrade job restarts k3s on
        # the host. When drain is specified the node is cordoned automatically
        # and uncordoned once the upgrade completes successfully.
        "drain": {
            # Delete bare pods not managed by a controller instead of
            # failing the drain. The controller already passes
            # --ignore-daemonsets and --delete-emptydir-data by default.
            "force": True,
            # On a 3-node cluster a PodDisruptionBudget can refuse evictions
            # forever and hang the upgrade, so delete pods directly (still a
            # graceful delete honoring terminationGracePeriodSeconds) and
            # stop waiting on pods stuck terminating.
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
        version=KURED_CHART_VERSION,
        namespace="kured",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://kubereboot.github.io/charts",
        ),
        values={
            "configuration": {
                # One node at a time: losing two of 3 control-plane nodes at once takes etcd down.
                "concurrency": 1,
                # Abort a PDB-hung drain after 10m; kured releases the lock and retries next period.
                "drainTimeout": "10m",
                "skipWaitForDeleteTimeout": 60,
                # No reboot window: nodes reboot once an update is staged; set startTime/endTime/timeZone for a window.
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
        version=KUBE_VIP_CHART_VERSION,
        namespace="kube-system",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://kube-vip.github.io/helm-charts",
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
