"""A Kubernetes Python Pulumi program to create a Kubernetes namespace and secret with PKO"""

from pulumi_kubernetes import core, rbac
from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.core.v1 import Namespace, Secret
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs, RepositoryOptsArgs
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

from pulumi import Config, ResourceOptions

# Create new Kubernetes Namespaces
namespace = Namespace(
    "pulumi-kubernetes-operator",
    metadata=ObjectMetaArgs(name="pulumi-kubernetes-operator"),
)
namespace = Namespace(
    "infra-apps",
    metadata=ObjectMetaArgs(name="infra-apps"),
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
        chart="oci://ghcr.io/pulumi/helm-charts/pulumi-kubernetes-operator",
        namespace="pulumi-kubernetes-operator",
        version="2.0.0",
        create_namespace=True,  # Optional: creates namespace if it doesn't exist
        timeout=600,
        atomic=True,  # Rollback on failure
    ),
)

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
        "stack": "bmbeverst/infra-apps",
        "projectRepo": "https://gitlab.com/bmbeverst/proxmox-k3s-cluster.git",
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
    },
    opts=ResourceOptions(depends_on=[pko]),
)

# infra_bootstrap = CustomResource(
#     "infra-bootstrap",
#     metadata={
#         "name": "infra-bootstrap",
#         "namespace": "pulumi-kubernetes-operator",
#     },
#     api_version="pulumi.com/v1",
#     kind="Stack",
#     spec={
#         "serviceAccountName": "pulumi",
#         "envRefs": {
#             "PULUMI_ACCESS_TOKEN": {
#                 "type": "Secret",
#                 "secret": {
#                     "name": pulumi_api_key.metadata.name,
#                     "key": "access_token",
#                 },
#             },
#         },
#         "stack": "bmbeverst/infra-bootstrap",
#         "projectRepo": "https://gitlab.com/bmbeverst/proxmox-k3s-cluster.git",
#         "repoDir": "pulumi/infra-bootstrap/",
#         "branch": "main",
#         "gitAuth": {
#             "accessToken": {
#                 "type": "Secret",
#                 "secret": {
#                     "name": gitlab_api_key.metadata.name,
#                     "key": "access_token",
#                 },
#             },
#         },
#         "destroyOnFinalize": True,
#     },
#     opts=ResourceOptions(depends_on=[pko]),
# )
