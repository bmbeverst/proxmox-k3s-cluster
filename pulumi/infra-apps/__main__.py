"""These are the apps that are setup to run the cluster infrastructure"""

from typing import Optional
import pulumi
import pulumi_kubernetes as k8s

from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.yaml.v2 import ConfigFile

from pulumi import ResourceOptions


SUC_VERSION = "v0.19.2"



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