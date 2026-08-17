"""User-facing applications deployed on the k3s cluster.

Currently provides the Ghost blog:
- SQLite-backed (no DB service; follows the locked-in decision).
- Official Ghost image, alpine tag pinned.
- Content persisted on the replicated `linstor-r2` StorageClass so a pod
  reschedules cleanly when a node dies.
- Local + insecure access only (ClusterIP + NodePort, no ingress/TLS yet).
"""

import pulumi
import pulumi_kubernetes as k8s
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

# Full image ref kept as ONE literal so Renovate's built-in `docker` manager
# (fileMatch in renovate.json) can bump the tag — no customManager needed.
GHOST_IMAGE = "ghost:6.57.1-alpine"

NAMESPACE = "my-apps"

# One canonical URL for Ghost (redirects/admin/canonical links need a single
# value) that must be reachable from INSIDE the pod too: Ghost's homepage
# probes assets at its own canonical `url` (image-size checks). A NodePort on a
# NODE IP is reachable from both the browser and from pods (kube-proxy), whereas
# the kube-vip control-plane VIP (10.10.1.99) was NOT reachable from pods and
# made the homepage hang (crash loop). Node IP is a single-node weak point for
# browser access — accepted for this no-ingress local pass; the durable fix is
# the deferred Traefik/Cloudflare ingress. Any of the 3 nodes serves NodePort.
GHOST_NODE_IP = "10.10.1.111"
GHOST_NODE_PORT = "31680"
GHOST_URL = f"http://{GHOST_NODE_IP}:{GHOST_NODE_PORT}"

namespace = k8s.core.v1.Namespace(
    "my-apps",
    metadata=ObjectMetaArgs(name=NAMESPACE),
)

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
                            # Bind 0.0.0.0 so the Service/NodePort can reach it.
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
                node_port=31680,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[namespace, ghost]),
)
