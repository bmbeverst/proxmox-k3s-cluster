"""Spoolman filament-spool manager, own `spoolman` namespace.

- Upstream image, pinned in `versions.yaml`. SQLite, no DB service.
- LAN VIP only (10.10.1.63), no auth.
- Nightly CronJob snapshots to NFS. WAL mode.
"""

import os

import pulumi
import pulumi_kubernetes as k8s
import pulumi_kubernetes.batch as k8s_batch  # for the backup CronJob
import yaml
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

# Read from repo; changes ship via git.
with open(
    os.path.join(os.path.dirname(__file__), "pod-scripts", "spoolman-backup.py")
) as f:
    SPOOLMAN_BACKUP_SCRIPT = f.read()

SPOOLMAN_IMAGE = f"ghcr.io/donkie/spoolman:{_versions['spoolman_image_tag']}"

NAMESPACE = "spoolman"

SPOOLMAN_PORT = 8000
# Image `app` user; gosu-drops from root.
SPOOLMAN_UID = 1000
# Holds spoolman.db.
SPOOLMAN_DATA_DIR = "/home/app/.local/share/spoolman"
SPOOLMAN_DATA_SIZE = "1Gi"
# kube-vip LB VIP, LAN only.
SPOOLMAN_LB_IP = "10.10.1.63"
# DNS-rebinding guard allowlist.
SPOOLMAN_ALLOWED_HOST = SPOOLMAN_LB_IP
# Same NFS export as vints, own subdir.
SPOOLMAN_NFS_SERVER = "10.10.1.101"
SPOOLMAN_NFS_PATH = "/pvebackup/spoolman"
# Backup joins via vints gid 1001.
VINTS_NFS_GID = 1001
SPOOLMAN_BACKUP_KEEP = 7


def register(namespace):
    """Create PVC, Deployment, Service, backup CronJob."""

    # SQLite on linstor-r2, survives node loss.
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
            # RWO: maxSurge=0 avoids double writers.
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
                    priority_class_name="app-low",
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
                                    "value": SPOOLMAN_ALLOWED_HOST,
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

    # LAN LoadBalancer on a static kube-vip VIP. No Tailscale: Spoolman is
    # local-network only, so it takes a VIP from the pool instead of an ingress.
    spoolman_service = k8s.core.v1.Service(
        "spoolman",
        metadata=ObjectMetaArgs(name="spoolman", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "spoolman"},
            type="LoadBalancer",
            load_balancer_ip=SPOOLMAN_LB_IP,
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

    # Script ships in the manifest, so changing it needs no image build.
    spoolman_backup_script = k8s.core.v1.ConfigMap(
        "spoolman-backup-script",
        metadata=ObjectMetaArgs(name="spoolman-backup-script", namespace=NAMESPACE),
        data={"spoolman-backup.py": SPOOLMAN_BACKUP_SCRIPT},
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # Nightly snapshot to NFS. 01:00, an hour after the vints backup.
    # No scale-to-zero: SQLite backup API reads through the WAL while app serves.
    spoolman_backup = k8s_batch.v1.CronJob(
        "spoolman-backup",
        metadata=ObjectMetaArgs(name="spoolman-backup", namespace=NAMESPACE),
        spec=k8s_batch.v1.CronJobSpecArgs(
            schedule="0 1 * * *",
            time_zone="America/New_York",
            concurrency_policy="Forbid",
            job_template=k8s_batch.v1.JobTemplateSpecArgs(
                spec=k8s_batch.v1.JobSpecArgs(
                    # One attempt: a retry just re-snapshots.
                    backoff_limit=0,
                    active_deadline_seconds=1800,
                    # Job deletes itself a day later.
                    ttl_seconds_after_finished=86400,
                    template=k8s.core.v1.PodTemplateSpecArgs(
                        metadata=ObjectMetaArgs(labels={"app": "spoolman-backup"}),
                        spec=k8s.core.v1.PodSpecArgs(
                            restart_policy="Never",
                            # uid 1000: WAL reads need -shm write. fs_group for PV.
                            # gid 1001: NFS export grant. Groups work, no host uid.
                            security_context=k8s.core.v1.PodSecurityContextArgs(
                                run_as_user=SPOOLMAN_UID,
                                run_as_group=SPOOLMAN_UID,
                                run_as_non_root=True,
                                fs_group=SPOOLMAN_UID,
                                supplemental_groups=[VINTS_NFS_GID],
                            ),
                            containers=[
                                k8s.core.v1.ContainerArgs(
                                    name="spoolman-backup",
                                    # App image has python3 + sqlite3; no entrypoint.
                                    image=SPOOLMAN_IMAGE,
                                    image_pull_policy="IfNotPresent",
                                    command=["python3", "/scripts/spoolman-backup.py"],
                                    env=[
                                        {
                                            "name": "DATA_PATH",
                                            "value": SPOOLMAN_DATA_DIR,
                                        },
                                        {"name": "BACKUP_DEST", "value": "/backups"},
                                        {
                                            "name": "KEEP",
                                            "value": str(SPOOLMAN_BACKUP_KEEP),
                                        },
                                        # Rootfs is read-only; no bytecode.
                                        {
                                            "name": "PYTHONDONTWRITEBYTECODE",
                                            "value": "1",
                                        },
                                    ],
                                    resources=k8s.core.v1.ResourceRequirementsArgs(
                                        requests={"cpu": "50m", "memory": "128Mi"},
                                        limits={"cpu": "500m", "memory": "256Mi"},
                                    ),
                                    security_context=k8s.core.v1.SecurityContextArgs(
                                        run_as_user=SPOOLMAN_UID,
                                        run_as_group=SPOOLMAN_UID,
                                        run_as_non_root=True,
                                        allow_privilege_escalation=False,
                                        read_only_root_filesystem=True,
                                        capabilities=k8s.core.v1.CapabilitiesArgs(
                                            drop=["ALL"]
                                        ),
                                    ),
                                    volume_mounts=[
                                        k8s.core.v1.VolumeMountArgs(
                                            name="spoolman-data",
                                            mount_path=SPOOLMAN_DATA_DIR,
                                        ),
                                        k8s.core.v1.VolumeMountArgs(
                                            name="spoolman-nfs",
                                            mount_path="/backups",
                                        ),
                                        k8s.core.v1.VolumeMountArgs(
                                            name="spoolman-backup-script",
                                            mount_path="/scripts/spoolman-backup.py",
                                            sub_path="spoolman-backup.py",
                                            read_only=True,
                                        ),
                                        k8s.core.v1.VolumeMountArgs(
                                            name="tmp",
                                            mount_path="/tmp",
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
                                # Direct NFS mount (kernel client).
                                k8s.core.v1.VolumeArgs(
                                    name="spoolman-nfs",
                                    nfs=k8s.core.v1.NFSVolumeSourceArgs(
                                        server=SPOOLMAN_NFS_SERVER,
                                        path=SPOOLMAN_NFS_PATH,
                                    ),
                                ),
                                k8s.core.v1.VolumeArgs(
                                    name="spoolman-backup-script",
                                    config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                                        name="spoolman-backup-script"
                                    ),
                                ),
                                k8s.core.v1.VolumeArgs(
                                    name="tmp",
                                    empty_dir=k8s.core.v1.EmptyDirVolumeSourceArgs(),
                                ),
                            ],
                        ),
                    ),
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            depends_on=[namespace, spoolman_storage, spoolman_backup_script]
        ),
    )
