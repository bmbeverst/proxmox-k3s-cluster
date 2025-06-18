"""A Kubernetes Python Pulumi program to create a Kubernetes namespace and secret with PKO"""

from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.core.v1 import Namespace, Secret
from pulumi_kubernetes.helm.v3 import Chart, ChartOpts
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

from pulumi import Config, ResourceOptions

# Create a new Kubernetes Namespace
namespace = Namespace(
    "pulumi-kubernetes-operator",
    metadata=ObjectMetaArgs(name="pulumi-kubernetes-operator"),
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

pko = Chart(
    "pulumi-kubernetes-operator",
    ChartOpts(
        chart="oci://ghcr.io/pulumi/helm-charts/pulumi-kubernetes-operator",
        namespace="pulumi-kubernetes-operator",
        version="2.0.0",
    ),
    opts=ResourceOptions(depends_on=[pulumi_api_key]),
)

infra_apps = CustomResource(
    "infra-apps",
    api_version="pulumi.com/v1",
    kind="Stack",
    # There is no way to check these values in the pulumi code
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
        "stack": "infra-apps/dev",
        "projectRepo": "https://gitlab.com/bmbeverst/proxmox-k3s-cluster.git",
        "repoDir": "pulumi/infra-apps/",
        "branch": "main",
        "destroyOnFinalize": True,
        "auth": {
            "type": "personalAccessToken",
            "personalAccessToken": gitlab_access_token,
        },
    },
    opts=ResourceOptions(depends_on=[pko]),
)

infra_bootstrap = CustomResource(
    "infra-bootstrap",
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
        "stack": "infra-bootstrap/dev",
        "projectRepo": "https://gitlab.com/bmbeverst/proxmox-k3s-cluster.git",
        "repoDir": "pulumi/infra-bootstrap/",
        "branch": "main",
        "destroyOnFinalize": True,
        "auth": {
            "type": "personalAccessToken",
            "personalAccessToken": gitlab_access_token,
        },
    },
    opts=ResourceOptions(depends_on=[pko]),
)
