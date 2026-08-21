"""A Kubernetes Python Pulumi program to create a Kubernetes namespace and secret with PKO"""

import os
import urllib.request

import yaml

from pulumi_kubernetes import core, rbac
from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.apiextensions.v1 import CustomResourceDefinition
from pulumi_kubernetes.core.v1 import Namespace, Secret
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

from pulumi import Config, ResourceOptions

# Chart repo/version live in ../Chart.yaml so Renovate's built-in helmv3 manager
# can bump the PKO chart version (the other charts live there too).
with open(os.path.join(os.path.dirname(__file__), "..", "Chart.yaml")) as f:
    _chart_deps = {d["name"]: d for d in yaml.safe_load(f)["dependencies"]}
_pko_chart = (
    _chart_deps["pulumi-kubernetes-operator"]["repository"]
    + "/"
    + _chart_deps["pulumi-kubernetes-operator"]["name"]
)

# PKO CRDs
PKO_CRDS_VERSION = "v" + _chart_deps["pulumi-kubernetes-operator"]["version"]

_PKO_CRD_FILES = [
    "pulumi.com_stacks.yaml",  # Stack CRs: infra-bootstrap / infra-apps / my-apps
    "pulumi.com_programs.yaml",
    "auto.pulumi.com_updates.yaml",
    "auto.pulumi.com_workspaces.yaml",
]


def _load_pko_crds() -> list:
    """Fetch the PKO CRD manifests pinned to PKO_CRDS_VERSION from the operator repo."""
    base = (
        "https://raw.githubusercontent.com/pulumi/pulumi-kubernetes-operator/"
        f"{PKO_CRDS_VERSION}/deploy/crds/"
    )
    docs = []
    for f in _PKO_CRD_FILES:
        url = base + f
        with urllib.request.urlopen(url, timeout=60) as r:
            docs.append(yaml.safe_load(r))
    return docs

# Create new Kubernetes Namespaces
namespace = Namespace(
    "pulumi-kubernetes-operator",
    metadata=ObjectMetaArgs(name="pulumi-kubernetes-operator"),
)
namespace = Namespace(
    "infra-apps",
    metadata=ObjectMetaArgs(name="infra-apps"),
)
namespace = Namespace(
    "my-apps",
    metadata=ObjectMetaArgs(name="my-apps"),
)

# Get the Pulumi API token.
pulumi_config = Config()
pulumi_access_token = pulumi_config.require_secret("pulumiAccessToken")
gitlab_access_token = pulumi_config.require_secret("gitlabAccessToken")


# Create a new Kubernetes Secret for the API Key
pulumi_api_key = Secret(
    "pulumi-access-token",
    metadata={
        "name": "pulumi-access-token",
        "namespace": "pulumi-kubernetes-operator",
    },
    string_data={"access_token": pulumi_access_token},
    opts=ResourceOptions(depends_on=[namespace]),
)

# Create a new Kubernetes Secret for the API Key
gitlab_api_key = Secret(
    "gitlab-access-token",
    metadata={
        "name": "gitlab-access-token",
        "namespace": "pulumi-kubernetes-operator",
    },
    string_data={"access_token": gitlab_access_token},
    opts=ResourceOptions(depends_on=[namespace]),
)

# Create ServiceAccount in the pulumi-kubernetes-operator namespace
service_account = core.v1.ServiceAccount(
    "pulumi-operator-sa",
    metadata={"name": "pulumi", "namespace": "pulumi-kubernetes-operator"},
)

# Create required ClusterRoleBinding for system:auth-delegator
auth_delegator_binding = rbac.v1.ClusterRoleBinding(
    "pulumi-operator-auth-delegator",
    metadata={"name": "pulumi-kubernetes-operator:pulumi:system:auth-delegator"},
    role_ref={
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "system:auth-delegator",
    },
    subjects=[
        {
            "kind": "ServiceAccount",
            "name": "pulumi",
            "namespace": "pulumi-kubernetes-operator",
        }
    ],
)

# Add cluster-admin permissions if your Stack manages Kubernetes resources
cluster_admin_binding = rbac.v1.ClusterRoleBinding(
    "pulumi-operator-cluster-admin",
    metadata={"name": "pulumi-kubernetes-operator:pulumi:cluster-admin"},
    role_ref={
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "cluster-admin",
    },
    subjects=[
        {
            "kind": "ServiceAccount",
            "name": "pulumi",
            "namespace": "pulumi-kubernetes-operator",
        }
    ],
)

pko = Release(
    "pulumi-kubernetes-operator",
    ReleaseArgs(
        chart=_pko_chart,
        namespace="pulumi-kubernetes-operator",
        version=_chart_deps["pulumi-kubernetes-operator"]["version"],
        create_namespace=True,  # Optional: creates namespace if it doesn't exist
        timeout=600,
        atomic=True,  # Rollback on failure
    ),
)

# PKO CRDs as durable Pulumi-managed resources
pko_crds = [
    CustomResourceDefinition(
        # Stable logical name -> stable URN for `pulumi import`.
        crd["metadata"]["name"].replace(".", "-") + "-crd",
        metadata=crd.get("metadata"),
        spec=crd["spec"],
        opts=ResourceOptions(
            protect=True,
            depends_on=[pko],
        ),
    )
    for crd in _load_pko_crds()
]

infra_apps = CustomResource(
    "infra-apps",
    metadata={
        "name": "infra-apps",
        "namespace": "pulumi-kubernetes-operator",
    },
    api_version="pulumi.com/v1",
    kind="Stack",
    # There is no way to check these values in the pulumi code
    # Best way is to look at the CRD: https://github.com/pulumi/pulumi-kubernetes-operator/blob/master/deploy/crds/pulumi.com_stacks.yaml
    spec={
        "serviceAccountName": "pulumi",
        "envRefs": {
            "PULUMI_ACCESS_TOKEN": {
                "type": "Secret",
                "secret": {
                    "name": pulumi_api_key.metadata.name,
                    "key": "access_token",
                },
            },
        },
        "stack": "bmbeverst/infra-apps/dev",
        "projectRepo": "https://gitlab.com/proxmox-k3s/proxmox-k3s-cluster.git",
        "repoDir": "pulumi/infra-apps/",
        "branch": "main",
        "gitAuth": {
            "accessToken": {
                "type": "Secret",
                "secret": {
                    "name": gitlab_api_key.metadata.name,
                    "key": "access_token",
                },
            },
        },
        "destroyOnFinalize": True,
        "refresh": True,
        "workspaceTemplate": {
            "spec": {
                "image": "pulumi/pulumi-python:latest",
                # pulumi/pulumi-python runs as root without $USER set; the
                # pulumi CLI (CGO_ENABLED=0) needs USER to resolve the home path.
                "env": [
                    {"name": "USER", "value": "root"},
                    {"name": "HOME", "value": "/share"},
                ],
            },
        },
    },
    opts=ResourceOptions(depends_on=[pko]),
)

# PKO self-manages its own release
infra_bootstrap = CustomResource(
    "infra-bootstrap",
    metadata={
        "name": "infra-bootstrap",
        "namespace": "pulumi-kubernetes-operator",
    },
    api_version="pulumi.com/v1",
    kind="Stack",
    spec={
        "serviceAccountName": "pulumi",
        "envRefs": {
            "PULUMI_ACCESS_TOKEN": {
                "type": "Secret",
                "secret": {
                    "name": pulumi_api_key.metadata.name,
                    "key": "access_token",
                },
            },
        },
        "stack": "bmbeverst/infra-bootstrap/dev",
        "projectRepo": "https://gitlab.com/proxmox-k3s/proxmox-k3s-cluster.git",
        "repoDir": "pulumi/infra-bootstrap/",
        "branch": "main",
        "gitAuth": {
            "accessToken": {
                "type": "Secret",
                "secret": {
                    "name": gitlab_api_key.metadata.name,
                    "key": "access_token",
                },
            },
        },
        "destroyOnFinalize": True,
        "refresh": True,
        "workspaceTemplate": {
            "spec": {
                "image": "pulumi/pulumi-python:latest",
                # pulumi/pulumi-python runs as root without $USER set; the
                # pulumi CLI (CGO_ENABLED=0) needs USER to resolve the home path.
                "env": [
                    {"name": "USER", "value": "root"},
                    {"name": "HOME", "value": "/share"},
                ],
            },
        },
    },
    opts=ResourceOptions(depends_on=[pko]),
)

# my-apps: user-facing apps, isolated from infra stacks.
my_apps = CustomResource(
    "my-apps",
    metadata={
        "name": "my-apps",
        "namespace": "pulumi-kubernetes-operator",
    },
    api_version="pulumi.com/v1",
    kind="Stack",
    spec={
        "serviceAccountName": "pulumi",
        "envRefs": {
            "PULUMI_ACCESS_TOKEN": {
                "type": "Secret",
                "secret": {
                    "name": pulumi_api_key.metadata.name,
                    "key": "access_token",
                },
            },
        },
        "stack": "bmbeverst/my-apps/dev",
        # my-apps requires the infra stacks to run first.
        "prerequisites": [
            {"name": "infra-bootstrap"},
            {"name": "infra-apps"},
        ],
        "projectRepo": "https://gitlab.com/proxmox-k3s/proxmox-k3s-cluster.git",
        "repoDir": "pulumi/my-apps/",
        "branch": "main",
        "gitAuth": {
            "accessToken": {
                "type": "Secret",
                "secret": {
                    "name": gitlab_api_key.metadata.name,
                    "key": "access_token",
                },
            },
        },
        "destroyOnFinalize": True,
        "refresh": True,
        "workspaceTemplate": {
            "spec": {
                "image": "pulumi/pulumi-python:latest",
                # Same USER/HOME workaround as infra-apps.
                "env": [
                    {"name": "USER", "value": "root"},
                    {"name": "HOME", "value": "/share"},
                ],
            },
        },
    },
    opts=ResourceOptions(depends_on=[pko]),
)
