"""Paseo (daemon + web UI) driving this repo, on k3s in its own `paseo` namespace.

- Image built from `containers/paseo/`, tag pinned in `versions.yaml`: Cline, Pulumi, uv, the
  `dangerous-command-guard` plugin (installed into `~/.cline` at start) and `paseo-ops-panel`.
- The init container runs `pod-scripts/paseo-init.sh`: read from the repo at run time (not baked
  into the image) and env-driven, so changes to it ship from git without a registry push.
- `paseo-home` keeps daemon state, Cline credentials and guard grants; `paseo-workspace`
  is the repos directory (this repo under `proxmox-k3s-cluster/`).
- Secrets: `paseoPassword` (daemon bearer token), `paseoClineProviders` (ClinePass bundle),
  `paseoGitToken` (clone + agent pushes, needs `write_repository`), `pulumiAccessToken` (panel preview).
- Hands-off: merge to `main`, PKO applies. Nothing applied by hand.
"""

import base64
import json
import os

import yaml

import pulumi
import pulumi_kubernetes as k8s
import pulumi_kubernetes.rbac as k8s_rbac  # read-only ClusterRole for in-cluster `pulumi preview`
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

NAMESPACE = "paseo"

PASEO_REGISTRY = "registry.gitlab.com"
PASEO_REGISTRY_IMAGE_ROOT = f"{PASEO_REGISTRY}/proxmox-k3s/proxmox-k3s-cluster"
PASEO_IMAGE = f"{PASEO_REGISTRY_IMAGE_ROOT}/paseo:{_versions['paseo_image_tag']}"

PASEO_PORT = 6767
# kube-vip LoadBalancer VIP (static). .60 traefik, .90 ghost and .91 vints are taken.
PASEO_LB_IP = "10.10.1.62"

# Repos cloned into the workspace PVC, one directory each. The token arrives via GIT_CONFIG_*
# env and is never written to .git/config; one token covers every repo listed here, so a repo
# it cannot reach needs its own entry (or a group access token).
PASEO_REPOS = [
    "https://gitlab.com/proxmox-k3s/proxmox-k3s-cluster.git",
]
PASEO_WORKSPACE = "/workspace"
# The clone lives one level down, so the PVC is a repos directory rather than a single checkout.
# Paseo projects are registered at a repo dir; it is also the panel cwd, so the first repo leads.
PASEO_REPO_DIRS = [
    f"{PASEO_WORKSPACE}/{url.rstrip('/').rsplit('/', 1)[-1].removesuffix('.git')}"
    for url in PASEO_REPOS
]
PASEO_REPO_DIR = PASEO_REPO_DIRS[0]
PASEO_HOME_DIR = "/home/paseo"

# The image's entrypoint copies this file into ~/.cline/data/settings/providers.json (fatal if absent).
# Not under /run/secrets: the kubelet mounts its SA token there read-only.
PASEO_CLINE_SEED_PATH = "/etc/paseo-secrets/cline-providers.json"

# uid/gid the image runs as (`paseo`); the init container hands the volumes over.
PASEO_UID = 1000
# Passed to paseo-init.sh as its single chown target.
PASEO_OWNER = f"{PASEO_UID}:{PASEO_UID}"

_paseo_cfg = pulumi.Config("my-apps")
_paseo_registry_user = _paseo_cfg.require_secret("vintsRegistryReadUser")
_paseo_registry_token = _paseo_cfg.require_secret("vintsRegistryReadToken")
_paseo_password = _paseo_cfg.require_secret("paseoPassword")
_paseo_cline_providers = _paseo_cfg.require_secret("paseoClineProviders")
_paseo_git_token = _paseo_cfg.require_secret("paseoGitToken")
_paseo_pulumi_token = _paseo_cfg.require_secret("pulumiAccessToken")


def _paseo_dockerconfigjson(user, token):
    return json.dumps({
        "auths": {
            PASEO_REGISTRY: {
                "username": user,
                "password": token,
                "auth": base64.b64encode(f"{user}:{token}".encode()).decode(),
            }
        }
    })


_paseo_dockerconfig = pulumi.Output.all(_paseo_registry_user, _paseo_registry_token).apply(
    lambda ut: _paseo_dockerconfigjson(ut[0], ut[1]))


def _paseo_git_env(token):
    """Git env for both containers: no credential ever lands on the PVC.

    `url.<authed>.insteadOf` rewrites the tokenless remote at use time; `safe.directory` lets
    uid 1000 read the trees the root init container cloned, one entry per repo.
    """
    env = [
        {"name": "GITLAB_TOKEN", "value": token},
        {"name": "GIT_TERMINAL_PROMPT", "value": "0"},
        {"name": "GIT_CONFIG_COUNT", "value": str(1 + len(PASEO_REPO_DIRS))},
        {
            "name": "GIT_CONFIG_KEY_0",
            "value": "url.https://oauth2:$(GITLAB_TOKEN)@gitlab.com/.insteadOf",
        },
        {"name": "GIT_CONFIG_VALUE_0", "value": "https://gitlab.com/"},
    ]
    for index, repo_dir in enumerate(PASEO_REPO_DIRS, start=1):
        env.append({"name": f"GIT_CONFIG_KEY_{index}", "value": "safe.directory"})
        env.append({"name": f"GIT_CONFIG_VALUE_{index}", "value": repo_dir})
    return env


# Runs in the init container (see its env below) before the daemon. It sits outside the image's
# build context, so this read keeps it manifest-carried: PKO ships a change to it without an
# image rebuild.
with open(os.path.join(os.path.dirname(__file__), "pod-scripts", "paseo-init.sh")) as f:
    PASEO_INIT_SCRIPT = f.read()


def register(namespace):
    """Create Paseo's Secrets, its two PVCs, the ServiceAccount, the Deployment and the Service.

    `namespace` is the `paseo` Namespace resource owned by __main__.py; ordering after the infra
    stacks comes from PKO's Stack CR `spec.prerequisites`, so the workloads only wait on it.
    """

    paseo_regcred = k8s.core.v1.Secret(
        "paseo-regcred",
        metadata=ObjectMetaArgs(name="paseo-regcred", namespace=NAMESPACE),
        type="kubernetes.io/dockerconfigjson",
        string_data={".dockerconfigjson": _paseo_dockerconfig},
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    paseo_secrets = k8s.core.v1.Secret(
        "paseo-secrets",
        metadata=ObjectMetaArgs(name="paseo-secrets", namespace=NAMESPACE),
        string_data={
            "paseo-password": _paseo_password,
            "pulumi-access-token": _paseo_pulumi_token,
        },
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # Seed providers.json with cline plus cline-pass only; other providers' keys stay off-cluster.
    paseo_cline_secret = k8s.core.v1.Secret(
        "paseo-cline-providers",
        metadata=ObjectMetaArgs(name="paseo-cline-providers", namespace=NAMESPACE),
        string_data={"providers.json": _paseo_cline_providers},
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # linstor-r2 (autoPlace: 2) survives a node loss. ReadWriteOnce: one replica only.
    paseo_home = k8s.core.v1.PersistentVolumeClaim(
        "paseo-home",
        metadata=ObjectMetaArgs(name="paseo-home", namespace=NAMESPACE),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],
            storage_class_name="linstor-r2",
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                requests={"storage": "5Gi"},
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    paseo_workspace = k8s.core.v1.PersistentVolumeClaim(
        "paseo-workspace",
        metadata=ObjectMetaArgs(name="paseo-workspace", namespace=NAMESPACE),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],
            storage_class_name="linstor-r2",
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                requests={"storage": "10Gi"},
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    paseo_sa = k8s.core.v1.ServiceAccount(
        "paseo",
        metadata=ObjectMetaArgs(name="paseo", namespace=NAMESPACE),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # Read-only ClusterRole: panel previews, and no apply path from this shell pod.
    paseo_read = k8s_rbac.v1.ClusterRole(
        "paseo-read",
        metadata=ObjectMetaArgs(name="paseo-read"),
        rules=[
            k8s_rbac.v1.PolicyRuleArgs(
                api_groups=["*"], resources=["*"], verbs=["get", "list", "watch"]),
        ],
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    paseo_read_binding = k8s_rbac.v1.ClusterRoleBinding(
        "paseo",
        metadata=ObjectMetaArgs(name="paseo"),
        role_ref=k8s_rbac.v1.RoleRefArgs(
            api_group="rbac.authorization.k8s.io", kind="ClusterRole", name="paseo-read"),
        subjects=[
            k8s_rbac.v1.SubjectArgs(kind="ServiceAccount", name="paseo", namespace=NAMESPACE),
        ],
        opts=pulumi.ResourceOptions(depends_on=[paseo_sa, paseo_read]),
    )

    paseo = k8s.apps.v1.Deployment(
        "paseo",
        metadata=ObjectMetaArgs(
            name="paseo",
            namespace=NAMESPACE,
            annotations={"reloader.stakater.com/auto": "true"},
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            # One replica on ReadWriteOnce volumes: delete-then-create, never two writers.
            strategy=k8s.apps.v1.DeploymentStrategyArgs(
                type="RollingUpdate",
                rolling_update=k8s.apps.v1.RollingUpdateDeploymentArgs(
                    max_surge=0, max_unavailable=1),
            ),
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "paseo"}),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(
                    labels={"app": "paseo"},
                    # Placeholder so the nested ignore below can resolve
                    annotations={"reloader.stakater.com/last-reloaded-from": "unset"},
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    service_account_name="paseo",
                    termination_grace_period_seconds=30,
                    # Group ownership for the volumes, so uid 1000 can write a fresh PVC.
                    security_context=k8s.core.v1.PodSecurityContextArgs(
                        fs_group=PASEO_UID,
                    ),
                    image_pull_secrets=[
                        k8s.core.v1.LocalObjectReferenceArgs(name="paseo-regcred"),
                    ],
                    init_containers=[
                        # Root only: chown the PVC, clone, then drop to uid 1000.
                        k8s.core.v1.ContainerArgs(
                            name="workspace",
                            image=PASEO_IMAGE,
                            image_pull_policy="IfNotPresent",
                            command=["/bin/sh", "-c"],
                            args=[PASEO_INIT_SCRIPT],
                            env=[
                                # Env-driven, so the script file stays static.
                                {"name": "PASEO_OWNER", "value": PASEO_OWNER},
                                {"name": "PASEO_HOME_DIR", "value": PASEO_HOME_DIR},
                                {"name": "PASEO_WORKSPACE", "value": PASEO_WORKSPACE},
                                {"name": "PASEO_REPOS", "value": " ".join(PASEO_REPOS)},
                                *_paseo_git_env(_paseo_git_token),
                            ],
                            security_context=k8s.core.v1.SecurityContextArgs(
                                run_as_user=0,
                                run_as_non_root=False,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="paseo-home", mount_path=PASEO_HOME_DIR),
                                k8s.core.v1.VolumeMountArgs(
                                    name="paseo-workspace", mount_path=PASEO_WORKSPACE),
                            ],
                        ),
                    ],
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="paseo",
                            image=PASEO_IMAGE,
                            image_pull_policy="IfNotPresent",
                            env=[
                                k8s.core.v1.EnvVarArgs(
                                    name="PASEO_PASSWORD",
                                    value_from=k8s.core.v1.EnvVarSourceArgs(
                                        secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                            name="paseo-secrets", key="paseo-password")),
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="PULUMI_ACCESS_TOKEN",
                                    value_from=k8s.core.v1.EnvVarSourceArgs(
                                        secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                            name="paseo-secrets", key="pulumi-access-token")),
                                ),
                                # The ops panel resolves the guard binary at plugin load.
                                {"name": "CLINE_GUARD_BIN",
                                 "value": "/opt/dangerous-command-guard/cline-approve-dangerous"},
                                {"name": "CLINE_PROVIDERS_SEED", "value": PASEO_CLINE_SEED_PATH},
                                {"name": "GIT_AUTHOR_NAME", "value": "paseo"},
                                {"name": "GIT_AUTHOR_EMAIL", "value": "paseo@localhost"},
                                *_paseo_git_env(_paseo_git_token),
                            ],
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="http", container_port=PASEO_PORT),
                            ],
                            # /api/health is unauthenticated; the daemon seeds volumes first, allow a slow start.
                            startup_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/api/health", port=PASEO_PORT),
                                period_seconds=5,
                                failure_threshold=24,
                                timeout_seconds=3,
                            ),
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/api/health", port=PASEO_PORT),
                                period_seconds=10,
                                failure_threshold=3,
                                timeout_seconds=3,
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/api/health", port=PASEO_PORT),
                                period_seconds=20,
                                failure_threshold=3,
                                timeout_seconds=5,
                            ),
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "250m", "memory": "1Gi"},
                                limits={"cpu": "2000m", "memory": "2Gi"},
                            ),
                            security_context=k8s.core.v1.SecurityContextArgs(
                                run_as_user=PASEO_UID,
                                run_as_group=PASEO_UID,
                                run_as_non_root=True,
                                allow_privilege_escalation=False,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="paseo-home", mount_path=PASEO_HOME_DIR),
                                k8s.core.v1.VolumeMountArgs(
                                    name="paseo-workspace", mount_path=PASEO_WORKSPACE),
                                k8s.core.v1.VolumeMountArgs(
                                    name="cline-seed", mount_path="/etc/paseo-secrets", read_only=True),
                            ],
                        ),
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="paseo-home",
                            persistent_volume_claim=k8s.core.v1.
                            PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="paseo-home"),
                        ),
                        k8s.core.v1.VolumeArgs(
                            name="paseo-workspace",
                            persistent_volume_claim=k8s.core.v1.
                            PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="paseo-workspace"),
                        ),
                        # 0444: copied as uid 1000; fsGroup may not reach a projected Secret.
                        k8s.core.v1.VolumeArgs(
                            name="cline-seed",
                            secret=k8s.core.v1.SecretVolumeSourceArgs(
                                secret_name="paseo-cline-providers",
                                default_mode=0o444,
                                items=[
                                    k8s.core.v1.KeyToPathArgs(
                                        key="providers.json", path="cline-providers.json"),
                                ],
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            depends_on=[namespace, paseo_regcred, paseo_secrets, paseo_cline_secret,
                        paseo_home, paseo_workspace, paseo_sa],
            ignore_changes=[
                'spec.template.metadata.annotations["reloader.stakater.com/last-reloaded-from"]',
            ],
        ),
    )

    # LoadBalancer on a static kube-vip VIP: the bundled web UI and the daemon API.
    paseo_service = k8s.core.v1.Service(
        "paseo",
        metadata=ObjectMetaArgs(name="paseo", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "paseo"},
            type="LoadBalancer",
            load_balancer_ip=PASEO_LB_IP,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=PASEO_PORT,
                    target_port=PASEO_PORT,
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, paseo]),
    )
