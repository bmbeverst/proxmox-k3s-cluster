"""Vintage Story dedicated server deployed on the k3s cluster

- Self-built image (`.NET 10` runtime + the pinned game baked in by CI)
  an init container only syncs mods every pod start, and the game
  version is whatever the image tag says.
- World/config/mods persisted on the StorageClass
- Backup = a CronJob at midnight ET: stops the Deployment, tars the world + config to an
  NFS share, starts the Deployment again.

"""

import base64
import json
import os

import yaml

import pulumi
import pulumi_kubernetes as k8s
import pulumi_kubernetes.batch as k8s_batch  # for the backup CronJob
import pulumi_kubernetes.rbac as k8s_rbac    # for the backup CronJob's RBAC
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

VINTS_PREFERRED_NODES = ["node1", "node2"]
VINTS_PORT = 42420
VINTS_LB_IP = "10.10.1.91"  # kube-vip LoadBalancer VIP (static)
VINTS_NFS_SERVER = "10.10.1.101"  # host serving the backup export
VINTS_NFS_PATH = "/pvebackup"     # export; writable by uid 1001

# ConfigMap: the mods list (direct .zip URLs, one per line; prefix with '#' to comment).
# Update = edit + push; a pod restart re-syncs it. The game version is not here - it is baked
# into the image tag.
_vints_mods = """\
# Format: one line per mod, "<mod-id> <direct .zip URL>". The <mod-id> is the
# install key -> $DATA_PATH/Mods/<id>.zip to prevent conflicts
sortablestorage https://mods.vintagestory.at/download/81763/sortablestorage_3.0.0.zip
hudclock https://mods.vintagestory.at/download/16782/hudclock-3.4.0.zip
automapmarkers https://mods.vintagestory.at/download/90054/Auto+Map+Markers+5.0.3+-+Vintage+Story+1.22.zip
"""


def _vints_env():
    # Shared env for both the init (mods) container and the main server container.
    return [
        {"name": "DATA_PATH", "value": "/data"},
        {"name": "PORT", "value": str(VINTS_PORT)},
    ]


def _vints_preferred_nodes():
    """Prefer the nodes holding a diskful vints-data replica, without pinning.
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
                                values=VINTS_PREFERRED_NODES,
                            ),
                        ],
                    ),
                ),
            ],
        ),
    )


def _vints_backup_placement():
    """Run the backup on the node that already has vints-data attached.

    The PVC is ReadWriteOnce, so a second pod on another node cannot attach it.
    """
    return k8s.core.v1.AffinityArgs(
        pod_affinity=k8s.core.v1.PodAffinityArgs(
            required_during_scheduling_ignored_during_execution=[
                k8s.core.v1.PodAffinityTermArgs(
                    topology_key="kubernetes.io/hostname",
                    label_selector=k8s.meta.v1.LabelSelectorArgs(
                        match_labels={"app": "vints"},
                    ),
                ),
            ],
        ),
    )


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

    # the init container only syncs mods every pod start (the game is baked into the image)
    # the main container then runs the server as PID 1.
    vints = k8s.apps.v1.Deployment(
        "vints",
        metadata=ObjectMetaArgs(name="vints", namespace=NAMESPACE),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            # delete-then-create instead of two servers on save
            strategy=k8s.apps.v1.DeploymentStrategyArgs(
                type="RollingUpdate",
                rolling_update=k8s.apps.v1.RollingUpdateDeploymentArgs(
                    max_surge=0, max_unavailable=1),
            ),
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "vints"}),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(labels={"app": "vints"}),
                spec=k8s.core.v1.PodSpecArgs(
                    affinity=_vints_preferred_nodes(),
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
                                    name="vints-data", mount_path="/data"),
                                k8s.core.v1.VolumeMountArgs(
                                    name="vints-config", mount_path="/config"),
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
                                    name="vints-data", mount_path="/data"),
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
                        k8s.core.v1.VolumeArgs(
                            name="vints-config",
                            config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                                name="vints-config")),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, vints_regcred, vints_data, vints_config]),
    )

    # LoadBalancer on static kube-vip VIP
    vints_service = k8s.core.v1.Service(
        "vints",
        metadata=ObjectMetaArgs(name="vints", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "vints"},
            type="LoadBalancer",
            load_balancer_ip=VINTS_LB_IP,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="game-tcp", port=VINTS_PORT, target_port=VINTS_PORT,
                    protocol="TCP"),
                k8s.core.v1.ServicePortArgs(
                    name="game-udp", port=VINTS_PORT, target_port=VINTS_PORT,
                    protocol="UDP"),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, vints]),
    )

    # Tailnet access via the Tailscale operator: ClusterIP + expose annotation, so
    # kube-vip and its IP pool are uninvolved. L3 ingress carries TCP and UDP.
    # Players reach it as vints-vintagestory.<tailnet>.ts.net (port 42420).
    vints_tailnet_service = k8s.core.v1.Service(
        "vints-tailnet",
        metadata=ObjectMetaArgs(
            name="vints-tailnet",
            namespace=NAMESPACE,
            annotations={
                "tailscale.com/expose": "true",
                "tailscale.com/hostname": "vints-vintagestory",
            },
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "vints"},
            type="ClusterIP",
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="game-tcp", port=VINTS_PORT, target_port=VINTS_PORT,
                    protocol="TCP"),
                k8s.core.v1.ServicePortArgs(
                    name="game-udp", port=VINTS_PORT, target_port=VINTS_PORT,
                    protocol="UDP"),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, vints]),
    )

    # The backup job stops the server, so it may scale this Deployment and watch its pods.
    vints_backup_sa = k8s.core.v1.ServiceAccount(
        "vints-backup",
        metadata=ObjectMetaArgs(name="vints-backup", namespace=NAMESPACE),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    vints_backup_role = k8s_rbac.v1.Role(
        "vints-backup",
        metadata=ObjectMetaArgs(name="vints-backup", namespace=NAMESPACE),
        rules=[
            k8s_rbac.v1.PolicyRuleArgs(
                api_groups=["apps"],
                resources=["deployments", "deployments/scale"],
                verbs=["get", "patch", "update"],
            ),
            k8s_rbac.v1.PolicyRuleArgs(
                api_groups=[""],
                resources=["pods"],
                verbs=["get", "list", "watch"],
            ),
        ],
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    vints_backup_binding = k8s_rbac.v1.RoleBinding(
        "vints-backup",
        metadata=ObjectMetaArgs(name="vints-backup", namespace=NAMESPACE),
        role_ref=k8s_rbac.v1.RoleRefArgs(
            api_group="rbac.authorization.k8s.io", kind="Role", name="vints-backup"),
        subjects=[
            k8s_rbac.v1.SubjectArgs(
                kind="ServiceAccount", name="vints-backup", namespace=NAMESPACE),
        ],
        opts=pulumi.ResourceOptions(depends_on=[vints_backup_sa, vints_backup_role]),
    )

    # Nightly: stop the Deployment, tar the world to NFS, start it again.
    # 00:00 America/New_York, so DST is the cluster's job.
    vints_backup = k8s.batch.v1.CronJob(
        "vints-backup",
        metadata=ObjectMetaArgs(name="vints-backup", namespace=NAMESPACE),
        spec=k8s.batch.v1.CronJobSpecArgs(
            schedule="0 0 * * *",
            time_zone="America/New_York",
            concurrency_policy="Forbid",
            job_template=k8s.batch.v1.JobTemplateSpecArgs(
                spec=k8s.batch.v1.JobSpecArgs(
                    # One attempt: a retry would stop the server again.
                    backoff_limit=0,
                    active_deadline_seconds=3600,
                    # The finished Job deletes itself a day later.
                    ttl_seconds_after_finished=86400,
                    template=k8s.core.v1.PodTemplateSpecArgs(
                        metadata=ObjectMetaArgs(labels={"app": "vints-backup"}),
                        spec=k8s.core.v1.PodSpecArgs(
                            restart_policy="Never",
                            service_account_name="vints-backup",
                            affinity=_vints_backup_placement(),
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
                                        {"name": "NAMESPACE", "value": NAMESPACE},
                                        {"name": "DEPLOYMENT", "value": "vints"},
                                        # kubectl needs a writable HOME; the rootfs is read-only.
                                        {"name": "HOME", "value": "/tmp"},
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
