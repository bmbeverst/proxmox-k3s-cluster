"""Vintage Story dedicated server deployed on the k3s cluster

- Self-built slim server image (`.NET 10` runtime + the install/update scripts);
  the binary + mods are installed/updated by an init container every pod start.
- World/config/mods persisted on the StorageClass
- Backup = a CronJob that hard-link snapshots `Saves/`, tars it and writes it to
  an NFS share outside the cluster.

"""

import base64
import json
import os

import yaml

import pulumi
import pulumi_kubernetes as k8s
import pulumi_kubernetes.batch as k8s_batch  # for the backup CronJob
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

NAMESPACE = "vintagestory"

# Images are built + pushed to the project GitLab registry by CI.
VINTS_REGISTRY = "registry.gitlab.com"
VINTS_REGISTRY_IMAGE_ROOT = f"{VINTS_REGISTRY}/proxmox-k3s/proxmox-k3s-cluster"
VINTS_IMAGE = f"{VINTS_REGISTRY_IMAGE_ROOT}/vints-server:{_versions['vints_server_image_tag']}"
VINTS_BACKUP_IMAGE = f"{VINTS_REGISTRY_IMAGE_ROOT}/vints-backup:{_versions['vints_backup_image_tag']}"

_vints_cfg = pulumi.Config("my-apps")
_vints_registry_user = _vints_cfg.require_secret("vintsRegistryReadUser")
_vints_registry_token = _vints_cfg.require_secret("vintsRegistryReadToken")


def _vints_dockerconfigjson(user, token):
    return json.dumps({
        "auths": {
            VINTS_REGISTRY: {
                "username": user,
                "password": token,
                "auth": base64.b64encode(f"{user}:{token}".encode()).decode(),
            }
        }
    })


_vints_dockerconfig = pulumi.Output.all(_vints_registry_user, _vints_registry_token).apply(
    lambda ut: _vints_dockerconfigjson(ut[0], ut[1]))

VS_VERSION = _versions["vintagestory_version"]
VINTS_NODE = "node2"       # temp setting for testing
VINTS_PORT = 42420
VINTS_NODE_PORT = 30420
VINTS_NFS_SERVER = "10.10.1.101"  # host serving the backup export
VINTS_NFS_PATH = "/pvebackup"     # export; writable by uid 1001

# ConfigMap: pinned version env + the mods list (direct .zip URLs, one per line;
# prefix with '#' to comment). Update = edit + push; pod restart re-syncs both.
_vints_mods = """\
# Format: one line per mod, "<mod-id> <direct .zip URL>". The <mod-id> is the
# install key -> $DATA_PATH/Mods/<id>.zip (deliberately NOT the URL basename,
# which is often just /latest). Changing the URL re-downloads; keeping the id
# stable never collides. Prefix a line with '#' to disable a mod.
# primitivesurvival (Primitive Survival by SpearAndFang) pinned to v5.1.2 for VS 1.22.x.
primitivesurvival https://mods.vintagestory.at/download/115238/primitivesurvival_5.1.2.zip
"""


def _vints_env():
    # Shared env for both the init installer and the main server container.
    return [
        {"name": "VS_VERSION", "value": VS_VERSION},
        {"name": "DATA_PATH", "value": "/data"},
        {"name": "PORT", "value": str(VINTS_PORT)},
    ]


def register(namespace):
    """Create the Vintage Story Secret/ConfigMap/PVC, its Deployment + Service,
    and the backup CronJob
    """
    vints_regcred = k8s.core.v1.Secret(
        "vints-regcred",
        metadata=ObjectMetaArgs(name="vints-regcred", namespace=NAMESPACE),
        type="kubernetes.io/dockerconfigjson",
        string_data={".dockerconfigjson": _vints_dockerconfig},
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    vints_config = k8s.core.v1.ConfigMap(
        "vints-config",
        metadata=ObjectMetaArgs(name="vints-config", namespace=NAMESPACE),
        data={
            "VS_VERSION": VS_VERSION,
            "PORT": str(VINTS_PORT),
            "mods.txt": _vints_mods,
        },
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # World/data lives on the replicated StorageClass so it survives a node death.
    vints_data = k8s.core.v1.PersistentVolumeClaim(
        "vints-data",
        metadata=ObjectMetaArgs(name="vints-data", namespace=NAMESPACE),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],
            storage_class_name="linstor-r2",
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                requests={"storage": "16Gi"},
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # the init container installs + updates server and mods every pod start
    # the main container then runs the server as PID 1.
    vints = k8s.apps.v1.Deployment(
        "vints",
        metadata=ObjectMetaArgs(name="vints", namespace=NAMESPACE),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "vints"}),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(labels={"app": "vints"}),
                spec=k8s.core.v1.PodSpecArgs(
                    node_selector={"kubernetes.io/hostname": VINTS_NODE},
                    termination_grace_period_seconds=30,
                    security_context=k8s.core.v1.PodSecurityContextArgs(
                        run_as_user=1001,
                        run_as_non_root=True,
                        fs_group=1001,
                    ),
                    image_pull_secrets=[
                        k8s.core.v1.LocalObjectReferenceArgs(name="vints-regcred"),
                    ],
                    init_containers=[
                        k8s.core.v1.ContainerArgs(
                            name="vints-installer",
                            image=VINTS_IMAGE,
                            image_pull_policy="Always",
                            command=["/entrypoints/install-vints.sh"],
                            env=_vints_env(),
                            security_context=k8s.core.v1.SecurityContextArgs(
                                run_as_non_root=True,
                                allow_privilege_escalation=False,
                                read_only_root_filesystem=True,
                                capabilities=k8s.core.v1.CapabilitiesArgs(
                                    drop=["ALL"]),
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="serverfiles", mount_path="/serverfiles"),
                                k8s.core.v1.VolumeMountArgs(
                                    name="vints-data", mount_path="/data"),
                                k8s.core.v1.VolumeMountArgs(
                                    name="vints-config", mount_path="/config"),
                                k8s.core.v1.VolumeMountArgs(
                                    name="tmp", mount_path="/tmp"),
                            ],
                        ),
                    ],
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="vints",
                            image=VINTS_IMAGE,
                            image_pull_policy="Always",
                            command=["/entrypoints/start-vints.sh"],
                            env=_vints_env(),
                            # `kubectl attach -it deploy/vints -c vints` server's interactive console.
                            stdin=True,
                            tty=True,
                            security_context=k8s.core.v1.SecurityContextArgs(
                                run_as_non_root=True,
                                allow_privilege_escalation=False,
                                capabilities=k8s.core.v1.CapabilitiesArgs(
                                    drop=["ALL"]),
                            ),
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="game-tcp", container_port=VINTS_PORT,
                                    protocol="TCP"),
                                k8s.core.v1.ContainerPortArgs(
                                    name="game-udp", container_port=VINTS_PORT,
                                    protocol="UDP"),
                            ],
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "1000m", "memory": "2Gi"},
                                limits={"cpu": "2000m", "memory": "3Gi"},
                            ),
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                tcp_socket=k8s.core.v1.TCPSocketActionArgs(
                                    port=VINTS_PORT),
                                initial_delay_seconds=15,
                                period_seconds=10,
                                timeout_seconds=5,
                                failure_threshold=3,
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                tcp_socket=k8s.core.v1.TCPSocketActionArgs(
                                    port=VINTS_PORT),
                                initial_delay_seconds=120,  # world load can be slow
                                period_seconds=30,
                                timeout_seconds=5,
                                failure_threshold=3,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="serverfiles", mount_path="/serverfiles"),
                                k8s.core.v1.VolumeMountArgs(
                                    name="vints-data", mount_path="/data"),
                            ],
                        ),
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="serverfiles",
                            empty_dir=k8s.core.v1.EmptyDirVolumeSourceArgs()),
                        k8s.core.v1.VolumeArgs(
                            name="vints-data",
                            persistent_volume_claim=k8s.core.v1.
                            PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="vints-data"),
                        ),
                        k8s.core.v1.VolumeArgs(
                            name="vints-config",
                            config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                                name="vints-config")),
                        k8s.core.v1.VolumeArgs(
                            name="tmp",
                            empty_dir=k8s.core.v1.EmptyDirVolumeSourceArgs()),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, vints_regcred, vints_data, vints_config]),
    )

    # NodePort so LAN clients can join. TCP+UDP on the game port, both mapped to
    # one NodePort. Same single-node weak point as Ghost (no ingress/TLS yet).
    vints_service = k8s.core.v1.Service(
        "vints",
        metadata=ObjectMetaArgs(name="vints", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "vints"},
            type="NodePort",
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="game-tcp", port=VINTS_PORT, target_port=VINTS_PORT,
                    protocol="TCP", node_port=VINTS_NODE_PORT),
                k8s.core.v1.ServicePortArgs(
                    name="game-udp", port=VINTS_PORT, target_port=VINTS_PORT,
                    protocol="UDP", node_port=VINTS_NODE_PORT),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, vints]),
    )

    # Backup: a CronJob that snapshots + tars the world and writes it to an NFS share
    # outside the cluster. The PVC alone is NOT the safety net (bad mod / corrupt save).
    # Runs on the same node as the server so the RWO world PVC can be mounted; the NFS
    # share is RWM network storage mounted via a plain `nfs` volume (no PV/PVC).
    vints_backup = k8s.batch.v1.CronJob(
        "vints-backup",
        metadata=ObjectMetaArgs(name="vints-backup", namespace=NAMESPACE),
        spec=k8s.batch.v1.CronJobSpecArgs(
            schedule="0 3 * * *",           # daily 03:00 (cluster TZ — verify)
            concurrency_policy="Forbid",
            job_template=k8s.batch.v1.JobTemplateSpecArgs(
                spec=k8s.batch.v1.JobSpecArgs(
                    backoff_limit=2,
                    active_deadline_seconds=3600,
                    template=k8s.core.v1.PodTemplateSpecArgs(
                        metadata=ObjectMetaArgs(labels={"app": "vints-backup"}),
                        spec=k8s.core.v1.PodSpecArgs(
                            restart_policy="OnFailure",
                            node_selector={"kubernetes.io/hostname": VINTS_NODE},
                            security_context=k8s.core.v1.PodSecurityContextArgs(
                                fs_group=1001, run_as_user=1001),
                            image_pull_secrets=[
                                k8s.core.v1.LocalObjectReferenceArgs(name="vints-regcred"),
                            ],
                            containers=[
                                k8s.core.v1.ContainerArgs(
                                    name="vints-backup",
                                    image=VINTS_BACKUP_IMAGE,
                                    image_pull_policy="Always",
                                    env=[
                                        {"name": "DATA_PATH", "value": "/data"},
                                        {"name": "BACKUP_DEST", "value": "/backups"},
                                    ],
                                    resources=k8s.core.v1.ResourceRequirementsArgs(
                                        requests={"cpu": "200m", "memory": "128Mi"},
                                        limits={"cpu": "1000m", "memory": "512Mi"},
                                    ),
                                    security_context=k8s.core.v1.SecurityContextArgs(
                                        run_as_non_root=True,
                                        allow_privilege_escalation=False,
                                        read_only_root_filesystem=True,
                                        capabilities=k8s.core.v1.CapabilitiesArgs(
                                            drop=["ALL"]),
                                    ),
                                    volume_mounts=[
                                        k8s.core.v1.VolumeMountArgs(
                                            name="vints-data", mount_path="/data"),
                                        k8s.core.v1.VolumeMountArgs(
                                            name="vints-nfs", mount_path="/backups"),
                                        k8s.core.v1.VolumeMountArgs(
                                            name="tmp", mount_path="/tmp"),
                                    ],
                                ),
                            ],
                            volumes=[
                                k8s.core.v1.VolumeArgs(
                                    name="vints-data",
                                    persistent_volume_claim=k8s.core.v1.
                                    PersistentVolumeClaimVolumeSourceArgs(
                                        claim_name="vints-data"),
                                ),
                                # Direct NFS mount (kernel client); writable by uid 1001.
                                k8s.core.v1.VolumeArgs(
                                    name="vints-nfs",
                                    nfs=k8s.core.v1.NFSVolumeSourceArgs(
                                        server=VINTS_NFS_SERVER,
                                        path=VINTS_NFS_PATH),
                                ),
                                k8s.core.v1.VolumeArgs(
                                    name="tmp",
                                    empty_dir=k8s.core.v1.EmptyDirVolumeSourceArgs()),
                            ],
                        ),
                    ),
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )
