"""Ghost blog on k3s, own `ghost` namespace.

- SQLite-backed, no DB service.
- Content on linstor-r2 PVC.
- LB via kube-vip VIP.
"""

import os

import pulumi
import pulumi_kubernetes as k8s
import yaml
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

GHOST_IMAGE = f"ghost:{_versions['ghost_image_tag']}"

NAMESPACE = "ghost"

# Ghost URL via kube-vip VIP.
GHOST_LB_IP = "10.10.1.90"
GHOST_URL = f"http://{GHOST_LB_IP}"

def register(namespace):
    """Create workload, PVC, LB Service."""

    # Content at /var/lib/ghost/content, SQLite inside. linstor-r2.
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
            # RWO: maxSurge=0 avoids FailedAttachVolume.
            strategy=k8s.apps.v1.DeploymentStrategyArgs(
                type="RollingUpdate",
                rolling_update=k8s.apps.v1.RollingUpdateDeploymentArgs(
                    max_surge=0, max_unavailable=1
                ),
            ),
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "ghost"}),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(labels={"app": "ghost"}),
                spec=k8s.core.v1.PodSpecArgs(
                    priority_class_name="app-high",
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

    # LoadBalancer on static kube-vip VIP
    ghost_service = k8s.core.v1.Service(
        "ghost",
        metadata=ObjectMetaArgs(name="ghost", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "ghost"},
            type="LoadBalancer",
            load_balancer_ip=GHOST_LB_IP,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=80,
                    target_port=2368,
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, ghost]),
    )
