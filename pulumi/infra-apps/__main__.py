"""These are the apps that are setup to run the cluster infrastructure"""

from typing import Optional
import pulumi
import pulumi_kubernetes as k8s

from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.yaml.v2 import ConfigFile

from pulumi import ResourceOptions


SUC_VERSION = "v0.19.2"


def patch_suc_deployment(
    args: pulumi.ResourceTransformationArgs,
) -> Optional[pulumi.ResourceTransformationResult]:
    # Only touch the Deployment, leave other resources in the manifest alone
    if args.type_ != "kubernetes:apps/v1:Deployment":
        return None

    props = args.props
    template_spec = props["spec"]["template"]["spec"]

    # Add volume mounts to all containers (there's only one in the SUC deployment)
    for container in template_spec.get("containers", []):
        container.setdefault("volumeMounts", []).extend(
            [
                {
                    "mountPath": "/usr/share/ssl",
                    "name": "usr-share-ssl",
                    "readOnly": True,
                },
                {
                    "mountPath": "/usr/share/ca-certificates",
                    "name": "usr-share-ca-certificates",
                    "readOnly": True,
                },
            ]
        )

    # Add the corresponding host path volumes
    template_spec.setdefault("volumes", []).extend(
        [
            {
                "hostPath": {"path": "/usr/share/ssl", "type": "DirectoryOrCreate"},
                "name": "usr-share-ssl",
            },
            {
                "hostPath": {
                    "path": "/usr/share/ca-certificates",
                    "type": "DirectoryOrCreate",
                },
                "name": "usr-share-ca-certificates",
            },
        ]
    )

    return pulumi.ResourceTransformationResult(props=props, opts=args.opts)


suc_crd = k8s.yaml.v2.ConfigFile(
    "system-upgrade-controller-crd",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/crd.yaml",
)

suc = k8s.yaml.v2.ConfigFile(
    "system-upgrade-controller",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/system-upgrade-controller.yaml",
    opts=pulumi.ResourceOptions(
        depends_on=[suc_crd],
        transformations=[patch_suc_deployment],
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
