"""Ghost blog deployed on the k3s cluster (its own `ghost` namespace).

- SQLite-backed (no DB service; follows the locked-in decision).
"""

import os

import yaml

import pulumi
import pulumi_kubernetes as k8s
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

GHOST_IMAGE = f"ghost:{_versions['ghost_image_tag']}"

NAMESPACE = "ghost"

# One canonical URL for Ghost
GHOST_NODE = "node3"
GHOST_NODE_IP = "10.10.1.113"
GHOST_NODE_PORT = "31681"
GHOST_URL = f"http://{GHOST_NODE_IP}:{GHOST_NODE_PORT}"


def register(namespace):
    """Create the Ghost workload, its content PVC and the NodePort Service.

    `namespace` is the `ghost` Namespace resource owned by __main__.py.
    Ordering after the infra stacks is handled by PKO's Stack CR
    `spec.prerequisites`, so workloads here only depend on `namespace`.
    """

    # Ghost content lives in /var/lib/ghost/content; SQLite DB at .../content/data/ghost.db.
    # Use linstor-r2 (autoPlace: 2, allowRemoteVolumeAccess) so state survives node loss.
    ghost_storage = k8s.core.v1.PersistentVolumeClaim(
        "ghost-content",
        metadata=ObjectMetaArgs(name="ghost-content", namespace=NAMESPACE),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],
            storage_class_name="linstor-r2",
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                requests={"storage": "2Gi"},
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    ghost = k8s.apps.v1.Deployment(
        "ghost",
        metadata=ObjectMetaArgs(name="ghost", namespace=NAMESPACE),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "ghost"}),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(labels={"app": "ghost"}),
                spec=k8s.core.v1.PodSpecArgs(
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="ghost",
                            image=GHOST_IMAGE,
                            env=[
                                {"name": "NODE_ENV", "value": "production"},
                                {"name": "url", "value": GHOST_URL},
                                {"name": "database__client", "value": "sqlite3"},
                                {
                                    "name": "database__connection__filename",
                                    "value": "/var/lib/ghost/content/data/ghost.db",
                                },
                                {"name": "server__host", "value": "0.0.0.0"},
                                {"name": "server__port", "value": "2368"},
                            ],
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="http",
                                    container_port=2368,
                                ),
                            ],
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "100m", "memory": "256Mi"},
                                limits={"cpu": "500m", "memory": "768Mi"},
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/",
                                    port=2368,
                                ),
                                initial_delay_seconds=45,
                                period_seconds=20,
                                timeout_seconds=8,
                                failure_threshold=3,
                            ),
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/",
                                    port=2368,
                                ),
                                initial_delay_seconds=20,
                                period_seconds=15,
                                timeout_seconds=8,
                                failure_threshold=3,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="ghost-content",
                                    mount_path="/var/lib/ghost/content",
                                ),
                            ],
                        ),
                    ],
                    node_selector={"kubernetes.io/hostname": GHOST_NODE},
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="ghost-content",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="ghost-content",
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, ghost_storage]),
    )

    # ClusterIP + NodePort for local, insecure access this pass.
    ghost_service = k8s.core.v1.Service(
        "ghost",
        metadata=ObjectMetaArgs(name="ghost", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "ghost"},
            type="NodePort",
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=2368,
                    target_port=2368,
                    node_port=31681,
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, ghost]),
    )
