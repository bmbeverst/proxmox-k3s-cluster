"""Ghost blog deployed on the k3s cluster (its own `ghost` namespace).

- SQLite-backed (no DB service; follows the locked-in decision).
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

# One canonical URL for Ghost, served through Traefik; the VIP stays for LAN use.
GHOST_HOST = "www.lenatin.org"
GHOST_URL = f"https://{GHOST_HOST}"
# kube-vip VIP, LAN only.
GHOST_LB_IP = "10.10.1.90"

# The nodes holding a diskful ghost-content replica, read from LINSTOR
# (`linstor rd ... resources`: node2 and node3 hold the disk, node1 is the
# tiebreaker). Revisit if the volume is ever re-placed.
GHOST_PREFERRED_NODES = ["node2", "node3"]


def _ghost_preferred_nodes():
    """Prefer the nodes holding a diskful ghost-content replica, without pinning.

    A hard nodeSelector used to pin Ghost to node3 alone, so a node loss left it
    unschedulable and the choice went stale whenever LINSTOR re-placed the
    replicas. Preferred affinity keeps the local-disk bias while still letting
    the pod move (e.g. a kured reboot).
    """
    return k8s.core.v1.AffinityArgs(
        node_affinity=k8s.core.v1.NodeAffinityArgs(
            preferred_during_scheduling_ignored_during_execution=[
                k8s.core.v1.PreferredSchedulingTermArgs(
                    weight=100,
                    preference=k8s.core.v1.NodeSelectorTermArgs(
                        match_expressions=[
                            k8s.core.v1.NodeSelectorRequirementArgs(
                                key="kubernetes.io/hostname",
                                operator="In",
                                values=GHOST_PREFERRED_NODES,
                            ),
                        ],
                    ),
                ),
            ],
        ),
    )


def register(namespace):
    """Create the Ghost workload, its content PVC and the LoadBalancer Service.

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
            # One replica on a ReadWriteOnce volume: delete-then-create, never two
            # writers. The default surge=1 leaves the replacement Pending on
            # FailedAttachVolume ("already used by pod"), because the old pod
            # cannot release the volume until the new one is ready.
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
                            # Ghost redirects any request whose host/proto is not
                            # canonical, and the kubelet follows that 301 to a TLS
                            # port Ghost does not serve. Claim the canonical host.
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/",
                                    port=2368,
                                    http_headers=[
                                        k8s.core.v1.HTTPHeaderArgs(
                                            name="Host", value=GHOST_HOST
                                        ),
                                        k8s.core.v1.HTTPHeaderArgs(
                                            name="X-Forwarded-Proto", value="https"
                                        ),
                                    ],
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
                                    http_headers=[
                                        k8s.core.v1.HTTPHeaderArgs(
                                            name="Host", value=GHOST_HOST
                                        ),
                                        k8s.core.v1.HTTPHeaderArgs(
                                            name="X-Forwarded-Proto", value="https"
                                        ),
                                    ],
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
                    affinity=_ghost_preferred_nodes(),
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

    # Public entry: Traefik serves the wildcard (no secretName, so cert-manager
    # never mints a per-host cert); the default TLS policy requires Cloudflare's
    # client certificate, so no annotation is needed here.
    ghost_ingress = k8s.networking.v1.Ingress(
        "ghost",
        metadata=ObjectMetaArgs(name="ghost", namespace=NAMESPACE),
        spec=k8s.networking.v1.IngressSpecArgs(
            ingress_class_name="traefik",
            rules=[
                k8s.networking.v1.IngressRuleArgs(
                    host=GHOST_HOST,
                    http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                        paths=[
                            k8s.networking.v1.HTTPIngressPathArgs(
                                path="/",
                                path_type="Prefix",
                                backend=k8s.networking.v1.IngressBackendArgs(
                                    service=k8s.networking.v1.IngressServiceBackendArgs(
                                        name="ghost",
                                        port=k8s.networking.v1.ServiceBackendPortArgs(
                                            number=80
                                        ),
                                    )
                                ),
                            )
                        ]
                    ),
                )
            ],
            tls=[k8s.networking.v1.IngressTLSArgs(hosts=[GHOST_HOST])],
        ),
        opts=pulumi.ResourceOptions(
            depends_on=[namespace, ghost, ghost_service],
        ),
    )
