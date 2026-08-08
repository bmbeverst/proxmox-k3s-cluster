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


# Add the flatcar-specific SSL paths
def _fix_suc_ssl_paths(
    args: pulumi.ResourceTransformArgs,
) -> Optional[pulumi.ResourceTransformResult]:
    # Only touch the Deployment, leave other resources in the manifest alone
    if args.type_ != "kubernetes:apps/v1:Deployment":
        return None

    props = args.props
    template_spec = props["spec"]["template"]["spec"]

    mounts = [
        {"mountPath": "/usr/share/ssl", "name": "usr-share-ssl", "readOnly": True},
        {
            "mountPath": "/usr/share/ca-certificates",
            "name": "usr-share-ca-certificates",
            "readOnly": True,
        },
    ]
    volumes = [
        {
            "hostPath": {"path": "/usr/share/ssl", "type": "DirectoryOrCreate"},
            "name": "usr-share-ssl",
        },
        {
            "hostPath": {"path": "/usr/share/ca-certificates", "type": "DirectoryOrCreate"},
            "name": "usr-share-ca-certificates",
        },
    ]

    for container in template_spec.get("containers", []):
        existing = {m["name"] for m in container.get("volumeMounts", [])}
        container.setdefault("volumeMounts", []).extend(
            m for m in mounts if m["name"] not in existing
        )
    existing_volumes = {v["name"] for v in template_spec.get("volumes", [])}
    template_spec.setdefault("volumes", []).extend(
        v for v in volumes if v["name"] not in existing_volumes
    )

    return pulumi.ResourceTransformResult(props=props, opts=args.opts)


suc_crd = k8s.yaml.v2.ConfigFile(
    "system-upgrade-controller-crd",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/crd.yaml",
)

suc = k8s.yaml.v2.ConfigFile(
    "system-upgrade-controller",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/system-upgrade-controller.yaml",
    opts=pulumi.ResourceOptions(
        depends_on=[suc_crd],
        transforms=[_fix_suc_ssl_paths],
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


# Piraeus operator first: its chart ships the piraeus.io/v1 CRDs
# (installCRDs=true). The linstor-cluster chart below renders custom
# resources and fails with "no matches for kind LinstorCluster" if the
# CRDs are not registered yet.
piraeus_operator = k8s.helm.v3.Release(
    "piraeus-operator",
    k8s.helm.v3.ReleaseArgs(
        chart="piraeus",
        version=_chart_deps["piraeus"]["version"],
        namespace="piraeus-datastore",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["piraeus"]["repository"],
        ),
        values={
            "installCRDs": True,
        },
        timeout=600,
    ),
)


# PoC backing store is a file-thin pool on the root disk — the nodes have no
# spare disks; switch to lvmThinPool + source.hostDevices if dedicated disks
# are added to the VMs later.
piraeus = k8s.helm.v3.Release(
    "linstor-cluster",
    k8s.helm.v3.ReleaseArgs(
        chart="linstor-cluster",
        version=_chart_deps["linstor-cluster"]["version"],
        namespace="piraeus-datastore",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["linstor-cluster"]["repository"],
        ),
        values={
            "linstorSatelliteConfigurations": [
                {
                    "name": "storage-pool",
                    "storagePools": [
                        {
                            "name": "file-thin",
                            "fileThinPool": {
                                "directory": "/var/lib/linstor-pools/file-thin",
                            },
                        },
                    ],
                    # Flatcar: /usr is read-only and /usr/src does not exist,
                    # so drop the DRBD loader's bind mount (piraeus Flatcar how-to).
                    "podTemplate": {
                        "spec": {
                            "volumes": [
                                {"name": "usr-src", "$patch": "delete"},
                            ],
                            "initContainers": [
                                {
                                    "name": "drbd-module-loader",
                                    "volumeMounts": [
                                        {
                                            "mountPath": "/usr/src",
                                            "name": "usr-src",
                                            "$patch": "delete",
                                        },
                                    ],
                                },
                            ],
                        },
                    },
                },
            ],
            "storageClasses": [
                {
                    # local-path stays the default SC; linstor is opt-in per PVC
                    "name": "linstor-r2",
                    "reclaimPolicy": "Delete",
                    "allowVolumeExpansion": True,
                    "volumeBindingMode": "WaitForFirstConsumer",
                    "provisioner": "linstor.csi.linbit.com",
                    "parameters": {
                        # Two replicas across the three nodes; a node without a
                        # replica attaches diskless (allowRemoteVolumeAccess).
                        "linstor.csi.linbit.com/autoPlace": "2",
                        "linstor.csi.linbit.com/storagePool": "file-thin",
                        "linstor.csi.linbit.com/allowRemoteVolumeAccess": "true",
                    },
                },
            ],
        },
        timeout=600,
    ),
    opts=pulumi.ResourceOptions(depends_on=[piraeus_operator]),
)
