"""A Kubernetes Python Pulumi program"""

from pulumi_kubernetes.apps.v1 import Deployment

import pulumi

app_labels = {"app": "nginx"}

deployment = Deployment(
    "nginx",
    spec={
        "selector": {"match_labels": app_labels},
        "replicas": 1,
        "template": {
            "metadata": {"labels": app_labels},
            "spec": {"containers": [{"name": "nginx", "image": "nginx"}]},
        },
    },
)

pulumi.export("name", deployment.metadata["name"])
