"""Paseo daemon + web UI, own `paseo` namespace.

- Image from `containers/paseo/`, pinned in `versions.yaml`.
- Init runs `pod-scripts/paseo-init.sh` from repo.
- `paseo-home`: state, credentials. `paseo-workspace`: repos.
- Scales to zero via the KEDA HTTP add-on; the LB VIP fronts its interceptor.
- Secrets: paseoPassword, paseoClineProviders, paseoGitToken, pulumiAccessToken.
"""

import base64
import json
import os

import pulumi
import pulumi_kubernetes as k8s
import pulumi_kubernetes.rbac as k8s_rbac  # read-only ClusterRole for in-cluster `pulumi preview`
import yaml
from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

NAMESPACE = "paseo"

PASEO_REGISTRY = "registry.gitlab.com"
PASEO_REGISTRY_IMAGE_ROOT = f"{PASEO_REGISTRY}/proxmox-k3s/proxmox-k3s-cluster"
PASEO_IMAGE = f"{PASEO_REGISTRY_IMAGE_ROOT}/paseo:{_versions['paseo_image_tag']}"

PASEO_PORT = 6767
# kube-vip LB VIP: fronts the KEDA interceptor, not paseo itself.
PASEO_LB_IP = "10.10.1.62"
PASEO_HOST = "paseo.lenatin.org"

# KEDA HTTP add-on (installed by infra-apps).
KEDA_NAMESPACE = "keda"
INTERCEPTOR_SERVICE = "keda-add-ons-http-interceptor-proxy"
INTERCEPTOR_PORT = 8080
# Chart-stable interceptor pod labels.
INTERCEPTOR_SELECTOR = {
    "app.kubernetes.io/component": "interceptor",
    "app.kubernetes.io/name": "http-add-on",
}
# Idle time before the pod is removed: one hour.
PASEO_SCALEDOWN_SECONDS = 3600

# Repos cloned into workspace PVC.
PASEO_REPOS = [
    "https://gitlab.com/proxmox-k3s/proxmox-k3s-cluster.git",
    "https://gitlab.com/Reed-DRB/galactic-phos.git",
]
PASEO_WORKSPACE = "/workspace"
# Clone lives one level down.
PASEO_REPO_DIRS = [
    f"{PASEO_WORKSPACE}/{url.rstrip('/').rsplit('/', 1)[-1].removesuffix('.git')}"
    for url in PASEO_REPOS
]
PASEO_REPO_DIR = PASEO_REPO_DIRS[0]
PASEO_HOME_DIR = "/home/paseo"

# Entrypoint copies to providers.json.
PASEO_CLINE_SEED_PATH = "/etc/paseo-secrets/cline-providers.json"

# Image `paseo` uid/gid.
PASEO_UID = 1000
# Init chown target.
PASEO_OWNER = f"{PASEO_UID}:{PASEO_UID}"

_paseo_cfg = pulumi.Config("my-apps")
_paseo_registry_user = _paseo_cfg.require_secret("vintsRegistryReadUser")
_paseo_registry_token = _paseo_cfg.require_secret("vintsRegistryReadToken")
_paseo_password = _paseo_cfg.require_secret("paseoPassword")
_paseo_cline_providers = _paseo_cfg.require_secret("paseoClineProviders")
_paseo_git_token = _paseo_cfg.require_secret("paseoGitToken")
_paseo_pulumi_token = _paseo_cfg.require_secret("pulumiAccessToken")


def _paseo_dockerconfigjson(user, token):
    return json.dumps(
        {
            "auths": {
                PASEO_REGISTRY: {
                    "username": user,
                    "password": token,
                    "auth": base64.b64encode(f"{user}:{token}".encode()).decode(),
                }
            }
        }
    )


_paseo_dockerconfig = pulumi.Output.all(
    _paseo_registry_user, _paseo_registry_token
).apply(lambda ut: _paseo_dockerconfigjson(ut[0], ut[1]))


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


# Pod-side scripts, read from repo.
_POD_SCRIPTS = os.path.join(os.path.dirname(__file__), "pod-scripts")

# Init container script.
with open(os.path.join(_POD_SCRIPTS, "paseo-init.sh")) as f:
    PASEO_INIT_SCRIPT = f.read()

# PostStart: registers repos.
with open(os.path.join(_POD_SCRIPTS, "paseo-register-projects.sh")) as f:
    PASEO_REGISTER_SCRIPT = f.read()


def _paseo_route():
    "Hostname route at the interceptor."
    return {
        "match": f"Host(`{PASEO_HOST}`)",
        "kind": "Rule",
        "services": [{"name": INTERCEPTOR_SERVICE, "port": INTERCEPTOR_PORT}],
    }


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

    # Seed cline providers only.
    paseo_cline_secret = k8s.core.v1.Secret(
        "paseo-cline-providers",
        metadata=ObjectMetaArgs(name="paseo-cline-providers", namespace=NAMESPACE),
        string_data={"providers.json": _paseo_cline_providers},
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # linstor-r2, survives node loss.
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

    # Read-only ClusterRole.
    paseo_read = k8s_rbac.v1.ClusterRole(
        "paseo-read",
        metadata=ObjectMetaArgs(name="paseo-read"),
        rules=[
            k8s_rbac.v1.PolicyRuleArgs(
                api_groups=["*"], resources=["*"], verbs=["get", "list", "watch"]
            ),
        ],
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    paseo_read_binding = k8s_rbac.v1.ClusterRoleBinding(
        "paseo",
        metadata=ObjectMetaArgs(name="paseo"),
        role_ref=k8s_rbac.v1.RoleRefArgs(
            api_group="rbac.authorization.k8s.io", kind="ClusterRole", name="paseo-read"
        ),
        subjects=[
            k8s_rbac.v1.SubjectArgs(
                kind="ServiceAccount", name="paseo", namespace=NAMESPACE
            ),
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
            # RWO: maxSurge=0.
            strategy=k8s.apps.v1.DeploymentStrategyArgs(
                type="RollingUpdate",
                rolling_update=k8s.apps.v1.RollingUpdateDeploymentArgs(
                    max_surge=0, max_unavailable=1
                ),
            ),
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "paseo"}),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(
                    labels={"app": "paseo"},
                    # Placeholder so the nested ignore below can resolve
                    annotations={"reloader.stakater.com/last-reloaded-from": "unset"},
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    priority_class_name="app-medium",
                    service_account_name="paseo",
                    termination_grace_period_seconds=30,
                    # fsGroup for PVC writes.
                    security_context=k8s.core.v1.PodSecurityContextArgs(
                        fs_group=PASEO_UID,
                    ),
                    image_pull_secrets=[
                        k8s.core.v1.LocalObjectReferenceArgs(name="paseo-regcred"),
                    ],
                    init_containers=[
                        # Root: chown, clone, drop.
                        k8s.core.v1.ContainerArgs(
                            name="workspace",
                            image=PASEO_IMAGE,
                            image_pull_policy="IfNotPresent",
                            command=["/bin/sh", "-c"],
                            args=[PASEO_INIT_SCRIPT],
                            env=[
                                # Env-driven; script static.
                                {"name": "PASEO_OWNER", "value": PASEO_OWNER},
                                {"name": "PASEO_HOME_DIR", "value": PASEO_HOME_DIR},
                                {"name": "PASEO_WORKSPACE", "value": PASEO_WORKSPACE},
                                {"name": "PASEO_REPOS", "value": " ".join(PASEO_REPOS)},
                                *_paseo_git_env(_paseo_git_token),
                            ],
                            # Request keeps off BestEffort.
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "100m", "memory": "128Mi"},
                                limits={"cpu": "1000m", "memory": "512Mi"},
                            ),
                            security_context=k8s.core.v1.SecurityContextArgs(
                                run_as_user=0,
                                run_as_non_root=False,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="paseo-home", mount_path=PASEO_HOME_DIR
                                ),
                                k8s.core.v1.VolumeMountArgs(
                                    name="paseo-workspace", mount_path=PASEO_WORKSPACE
                                ),
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
                                            name="paseo-secrets", key="paseo-password"
                                        )
                                    ),
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="PULUMI_ACCESS_TOKEN",
                                    value_from=k8s.core.v1.EnvVarSourceArgs(
                                        secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                            name="paseo-secrets",
                                            key="pulumi-access-token",
                                        )
                                    ),
                                ),
                                # Guard binary for panel.
                                {
                                    "name": "CLINE_GUARD_BIN",
                                    "value": "/opt/dangerous-command-guard/cline-approve-dangerous",
                                },
                                {
                                    "name": "CLINE_PROVIDERS_SEED",
                                    "value": PASEO_CLINE_SEED_PATH,
                                },
                                # DNS-rebinding guard: the hostname route is not a default.
                                {"name": "PASEO_ALLOWED_HOSTS", "value": PASEO_HOST},
                                {"name": "GIT_AUTHOR_NAME", "value": "paseo"},
                                {
                                    "name": "GIT_AUTHOR_EMAIL",
                                    "value": "paseo@localhost",
                                },
                                # PostStart registers these.
                                {
                                    "name": "PASEO_REPO_DIRS",
                                    "value": " ".join(PASEO_REPO_DIRS),
                                },
                                *_paseo_git_env(_paseo_git_token),
                            ],
                            # PostStart: daemon not in init.
                            lifecycle=k8s.core.v1.LifecycleArgs(
                                post_start=k8s.core.v1.LifecycleHandlerArgs(
                                    exec_=k8s.core.v1.ExecActionArgs(
                                        command=["/bin/sh", "-c", PASEO_REGISTER_SCRIPT]
                                    ),
                                ),
                            ),
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="http", container_port=PASEO_PORT
                                ),
                            ],
                            # Slow start: seeds volumes.
                            startup_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/api/health", port=PASEO_PORT
                                ),
                                period_seconds=5,
                                failure_threshold=24,
                                timeout_seconds=3,
                            ),
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/api/health", port=PASEO_PORT
                                ),
                                period_seconds=10,
                                failure_threshold=3,
                                timeout_seconds=3,
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/api/health", port=PASEO_PORT
                                ),
                                period_seconds=20,
                                failure_threshold=3,
                                timeout_seconds=5,
                            ),
                            # Request 3Gi, limit 4Gi.
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "250m", "memory": "3Gi"},
                                limits={"cpu": "2000m", "memory": "4Gi"},
                            ),
                            security_context=k8s.core.v1.SecurityContextArgs(
                                run_as_user=PASEO_UID,
                                run_as_group=PASEO_UID,
                                run_as_non_root=True,
                                allow_privilege_escalation=False,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="paseo-home", mount_path=PASEO_HOME_DIR
                                ),
                                k8s.core.v1.VolumeMountArgs(
                                    name="paseo-workspace", mount_path=PASEO_WORKSPACE
                                ),
                                k8s.core.v1.VolumeMountArgs(
                                    name="cline-seed",
                                    mount_path="/etc/paseo-secrets",
                                    read_only=True,
                                ),
                            ],
                        ),
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="paseo-home",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="paseo-home"
                            ),
                        ),
                        k8s.core.v1.VolumeArgs(
                            name="paseo-workspace",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="paseo-workspace"
                            ),
                        ),
                        # 0444: projected Secret.
                        k8s.core.v1.VolumeArgs(
                            name="cline-seed",
                            secret=k8s.core.v1.SecretVolumeSourceArgs(
                                secret_name="paseo-cline-providers",
                                default_mode=0o444,
                                items=[
                                    k8s.core.v1.KeyToPathArgs(
                                        key="providers.json",
                                        path="cline-providers.json",
                                    ),
                                ],
                            ),
                        ),
                    ],
                    affinity=k8s.core.v1.NodeAffinityArgs(
                        preferred_during_scheduling_ignored_during_execution=[
                            k8s.core.v1.PreferredSchedulingTermArgs(
                                weight=100,
                                preference=k8s.core.v1.NodeSelectorTermArgs(
                                    match_expressions=[
                                        k8s.core.v1.NodeSelectorRequirementArgs(
                                            key="proxmox-host",
                                            operator="In",
                                            values=["pve"],
                                        ),
                                    ],
                                ),
                            ),
                        ],
                    ),
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            depends_on=[
                namespace,
                paseo_regcred,
                paseo_secrets,
                paseo_cline_secret,
                paseo_home,
                paseo_workspace,
                paseo_sa,
            ],
            ignore_changes=[
                # KEDA owns the replica count now (scale to zero).
                "spec.replicas",
                'spec.template.metadata.annotations["reloader.stakater.com/last-reloaded-from"]',
            ],
        ),
    )

    # ClusterIP: the interceptor is the only thing that talks to paseo.
    paseo_service = k8s.core.v1.Service(
        "paseo",
        metadata=ObjectMetaArgs(name="paseo", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "paseo"},
            type="ClusterIP",
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

    # The VIP now lands on the interceptor, which wakes paseo on the first request.
    paseo_vip = k8s.core.v1.Service(
        "paseo-interceptor",
        metadata=ObjectMetaArgs(name="paseo-interceptor", namespace=KEDA_NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector=INTERCEPTOR_SELECTOR,
            type="LoadBalancer",
            load_balancer_ip=PASEO_LB_IP,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=PASEO_PORT,
                    target_port="proxy",
                    protocol="TCP",
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[paseo_service]),
    )

    # Which hosts/paths wake paseo; health checks answer without waking it.
    paseo_route = CustomResource(
        "paseo-interceptor-route",
        api_version="http.keda.sh/v1beta1",
        kind="InterceptorRoute",
        metadata=ObjectMetaArgs(name="paseo", namespace=NAMESPACE),
        spec={
            "target": {"service": "paseo", "port": PASEO_PORT},
            "rules": [
                # Bare VIP: match any Host.
                {"paths": [{"value": "/"}]},
                # Hostname via Traefik.
                {"hosts": [PASEO_HOST], "paths": [{"value": "/"}]},
            ],
            "scalingMetric": {"concurrency": {"targetValue": 10}},
            # No request deadline: the UI holds long-lived streams.
            "timeouts": {
                "readiness": "120s",
                "request": "0s",
                "responseHeader": "30s",
            },
            "staticRoutes": [
                {
                    "rules": [{"paths": [{"value": "/api/health"}]}],
                    "response": {"statusCode": 200, "body": "OK"},
                }
            ],
        },
        opts=pulumi.ResourceOptions(depends_on=[namespace, paseo]),
    )

    # Request-driven scaling: 0 replicas after an hour idle.
    paseo_scaledobject = CustomResource(
        "paseo-scaledobject",
        api_version="keda.sh/v1alpha1",
        kind="ScaledObject",
        metadata=ObjectMetaArgs(name="paseo", namespace=NAMESPACE),
        spec={
            "minReplicaCount": 0,
            # RWO PVCs: never two pods.
            "maxReplicaCount": 1,
            "cooldownPeriod": PASEO_SCALEDOWN_SECONDS,
            "pollingInterval": 15,
            "scaleTargetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": "paseo",
            },
            "triggers": [
                {
                    "type": "external-push",
                    "metadata": {
                        "scalerAddress": "keda-add-ons-http-external-scaler.keda:9090",
                        "interceptorRoute": "paseo",
                    },
                }
            ],
        },
        opts=pulumi.ResourceOptions(depends_on=[namespace, paseo, paseo_route]),
    )

    # Plain http redirects to https on the hostname.
    paseo_redirect = CustomResource(
        "paseo-https-redirect",
        api_version="traefik.io/v1alpha1",
        kind="Middleware",
        metadata=ObjectMetaArgs(name="paseo-https-redirect", namespace=KEDA_NAMESPACE),
        spec={"redirectScheme": {"scheme": "https", "permanent": True}},
        opts=pulumi.ResourceOptions(depends_on=[paseo_vip]),
    )

    # The cluster default TLSOption requires a Cloudflare origin-pull client cert,
    # which would break LAN/Tailscale clients once http redirects to https.
    paseo_tls = CustomResource(
        "paseo-tls-option",
        api_version="traefik.io/v1alpha1",
        kind="TLSOption",
        metadata=ObjectMetaArgs(name="paseo", namespace=KEDA_NAMESPACE),
        spec={"minVersion": "VersionTLS12"},
        opts=pulumi.ResourceOptions(depends_on=[paseo_vip]),
    )

    # Hostname path. Traefik forbids cross-namespace service refs, so this lives in `keda`.
    # One IngressRoute per entrypoint: redirect on `web`, serve on `websecure`.
    paseo_ingress_web = CustomResource(
        "paseo-ingressroute-web",
        api_version="traefik.io/v1alpha1",
        kind="IngressRoute",
        metadata=ObjectMetaArgs(name="paseo-web", namespace=KEDA_NAMESPACE),
        spec={
            "entryPoints": ["web"],
            "routes": [
                {
                    **_paseo_route(),
                    "middlewares": [{"name": "paseo-https-redirect"}],
                }
            ],
        },
        opts=pulumi.ResourceOptions(depends_on=[paseo_redirect]),
    )

    paseo_ingress = CustomResource(
        "paseo-ingressroute",
        api_version="traefik.io/v1alpha1",
        kind="IngressRoute",
        metadata=ObjectMetaArgs(name="paseo", namespace=KEDA_NAMESPACE),
        spec={
            "entryPoints": ["websecure"],
            "routes": [_paseo_route()],
            "tls": {"options": {"name": "paseo"}},
        },
        opts=pulumi.ResourceOptions(depends_on=[paseo_tls]),
    )
