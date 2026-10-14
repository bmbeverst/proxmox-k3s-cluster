"""Spoolman filament-spool manager (own `spoolman` namespace).

- Upstream image, pinned in `versions.yaml`. SQLite only, no DB service.
- Spoolman has no auth. Tailnet-only via Tailscale ingress, no LAN VIP.
- Measured ~107 MiB idle, one uvicorn process. FilaMan used ~377 MiB, ten.
"""

import os

import yaml

import pulumi
import pulumi_kubernetes as k8s
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

SPOOLMAN_IMAGE = f"ghcr.io/donkie/spoolman:{_versions['spoolman_image_tag']}"

NAMESPACE = "spoolman"

SPOOLMAN_PORT = 8000
# Image's `app` user. Entrypoint gosu-drops root to this uid; volume must be writable.
SPOOLMAN_UID = 1000
# Holds spoolman.db. Upstream warns if this is not a mounted volume.
SPOOLMAN_DATA_DIR = "/home/app/.local/share/spoolman"
SPOOLMAN_DATA_SIZE = "1Gi"
# Tailnet name -> http://spoolman/. Also turns on Spoolman's host guard.
SPOOLMAN_TAILNET_HOST = "spoolman"


def register(namespace):
    """Create PVC, Deployment and tailnet-exposed Service.

    `namespace` is owned by __main__.py. Infra ordering is PKO `spec.prerequisites`.
    """

    # One SQLite file. linstor-r2 (autoPlace: 2) survives losing a node.
    spoolman_storage = k8s.core.v1.PersistentVolumeClaim(
        "spoolman-data",
        metadata=ObjectMetaArgs(name="spoolman-data", namespace=NAMESPACE),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],
            storage_class_name="linstor-r2",
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                requests={"storage": SPOOLMAN_DATA_SIZE},
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    spoolman = k8s.apps.v1.Deployment(
        "spoolman",
        metadata=ObjectMetaArgs(name="spoolman", namespace=NAMESPACE),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            # RWO volume: delete-then-create, never two writers on one SQLite file.
            strategy=k8s.apps.v1.DeploymentStrategyArgs(
                type="RollingUpdate",
                rolling_update=k8s.apps.v1.RollingUpdateDeploymentArgs(
                    max_surge=0, max_unavailable=1
                ),
            ),
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "spoolman"}),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(labels={"app": "spoolman"}),
                spec=k8s.core.v1.PodSpecArgs(
                    # Must start as root: entrypoint chowns data dir, then gosu-drops.
                    # No run_as_non_root. fs_group covers the PVC until that chown.
                    security_context=k8s.core.v1.PodSecurityContextArgs(
                        fs_group=SPOOLMAN_UID,
                    ),
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="spoolman",
                            image=SPOOLMAN_IMAGE,
                            image_pull_policy="IfNotPresent",
                            env=[
                                {"name": "SPOOLMAN_HOST", "value": "0.0.0.0"},
                                {"name": "SPOOLMAN_PORT", "value": str(SPOOLMAN_PORT)},
                                # Matches the image's `app` user: chown is a no-op.
                                {"name": "PUID", "value": str(SPOOLMAN_UID)},
                                {"name": "PGID", "value": str(SPOOLMAN_UID)},
                                {
                                    "name": "SPOOLMAN_ALLOWED_HOSTS",
                                    "value": SPOOLMAN_TAILNET_HOST,
                                },
                            ],
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="http",
                                    container_port=SPOOLMAN_PORT,
                                ),
                            ],
                            # Measured 107 MiB idle. Page cache reclaimable, no OOM.
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "50m", "memory": "128Mi"},
                                limits={"cpu": "500m", "memory": "256Mi"},
                            ),
                            # Health is unauthenticated; first boot migrates DB.
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/api/v1/health",
                                    port=SPOOLMAN_PORT,
                                ),
                                initial_delay_seconds=10,
                                period_seconds=15,
                                timeout_seconds=5,
                                failure_threshold=3,
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/api/v1/health",
                                    port=SPOOLMAN_PORT,
                                ),
                                initial_delay_seconds=30,
                                period_seconds=30,
                                timeout_seconds=5,
                                failure_threshold=3,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="spoolman-data",
                                    mount_path=SPOOLMAN_DATA_DIR,
                                ),
                            ],
                        ),
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="spoolman-data",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="spoolman-data"
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, spoolman_storage]),
    )

    # Tailscale L3 ingress: proxy joins tailnet as `spoolman`. No kube-vip VIP.
    spoolman_service = k8s.core.v1.Service(
        "spoolman",
        metadata=ObjectMetaArgs(
            name="spoolman",
            namespace=NAMESPACE,
            annotations={
                "tailscale.com/expose": "true",
                "tailscale.com/hostname": SPOOLMAN_TAILNET_HOST,
            },
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "spoolman"},
            type="LoadBalancer",
            load_balancer_class="tailscale",
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=80,
                    target_port=SPOOLMAN_PORT,
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, spoolman]),
    )
