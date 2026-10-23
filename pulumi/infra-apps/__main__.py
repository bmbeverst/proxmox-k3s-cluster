"""Cluster infra: SUC, kube-vip, LINSTOR, cert-manager, monitoring."""

import os
import urllib.request
from typing import Optional

import pulumi
import pulumi_kubernetes as k8s
import yaml
from pulumi import ResourceOptions
from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.yaml.v2 import ConfigFile

# SUC version from versions.yaml.
with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

SUC_VERSION = _versions["suc_version"]

# Helm chart versions from Chart.yaml.
with open(os.path.join(os.path.dirname(__file__), "..", "Chart.yaml")) as f:
    _chart_deps = {d["name"]: d for d in yaml.safe_load(f)["dependencies"]}


# Add the flatcar-specific SSL paths
def _fix_suc_ssl_paths(
    args: pulumi.ResourceTransformArgs,
) -> Optional[pulumi.ResourceTransformResult]:
    # Only touch the Deployment, leave other resources in the manifest alone
    if args.type_ != "kubernetes:apps/v1:Deployment":
        return None

    props = args.props
    template_spec = props["spec"]["template"]["spec"]

    mounts = [
        {"mountPath": "/usr/share/ssl", "name": "usr-share-ssl", "readOnly": True},
        {
            "mountPath": "/usr/share/ca-certificates",
            "name": "usr-share-ca-certificates",
            "readOnly": True,
        },
    ]
    volumes = [
        {
            "hostPath": {"path": "/usr/share/ssl", "type": "DirectoryOrCreate"},
            "name": "usr-share-ssl",
        },
        {
            "hostPath": {
                "path": "/usr/share/ca-certificates",
                "type": "DirectoryOrCreate",
            },
            "name": "usr-share-ca-certificates",
        },
    ]

    for container in template_spec.get("containers", []):
        existing = {m["name"] for m in container.get("volumeMounts", [])}
        container.setdefault("volumeMounts", []).extend(
            m for m in mounts if m["name"] not in existing
        )
    existing_volumes = {v["name"] for v in template_spec.get("volumes", [])}
    template_spec.setdefault("volumes", []).extend(
        v for v in volumes if v["name"] not in existing_volumes
    )

    return pulumi.ResourceTransformResult(props=props, opts=args.opts)


suc_crd = k8s.yaml.v2.ConfigFile(
    "system-upgrade-controller-crd",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/crd.yaml",
)

suc = k8s.yaml.v2.ConfigFile(
    "system-upgrade-controller",
    file=f"https://github.com/rancher/system-upgrade-controller/releases/download/{SUC_VERSION}/system-upgrade-controller.yaml",
    opts=pulumi.ResourceOptions(
        depends_on=[suc_crd],
        transforms=[_fix_suc_ssl_paths],
    ),
)

# Assigns pool IPs to LB services.
kvcp = k8s.yaml.v2.ConfigFile(
    "kube-vip-cloud-controller",
    file=f"https://raw.githubusercontent.com/kube-vip/kube-vip-cloud-provider/{_versions['kvcp_version']}/manifest/kube-vip-cloud-controller.yaml",
)

# Global pool for LoadBalancer service IP assignment (key: range-global).
# Must not overlap existing VIPs: .60 traefik, .90 ghost, .91 vints, .99 control-plane.
kvcp_pool = k8s.core.v1.ConfigMap(
    "kubevip",
    metadata={"name": "kubevip", "namespace": "kube-system"},
    data={"range-global": "10.10.1.61-10.10.1.80"},
    opts=ResourceOptions(depends_on=[kvcp]),
)
server_plan = CustomResource(
    "server-plan",
    api_version="upgrade.cattle.io/v1",
    kind="Plan",
    metadata={
        "name": "server-plan",
        "namespace": "system-upgrade",
    },
    spec={
        "concurrency": 1,
        "cordon": True,
        # Drain pods before k3s restart.
        "drain": {
            # Force-delete pods on drain.
            "force": True,
            # Delete pods if PDB blocks drain.
            "disableEviction": True,
            "skipWaitForDeleteTimeout": 60,
        },
        "nodeSelector": {
            "matchExpressions": [
                {
                    "key": "node-role.kubernetes.io/control-plane",
                    "operator": "In",
                    "values": ["true"],
                }
            ],
        },
        "serviceAccountName": "system-upgrade",
        "upgrade": {
            "image": "rancher/k3s-upgrade",
        },
        "channel": "https://update.k3s.io/v1-release/channels/stable",
    },
    opts=ResourceOptions(depends_on=[suc]),
)

agent_plan = CustomResource(
    "agent-plan",
    api_version="upgrade.cattle.io/v1",
    kind="Plan",
    metadata={
        "name": "agent-plan",
        "namespace": "system-upgrade",
    },
    spec={
        # Agent plan: worker nodes only.
        "concurrency": 1,
        "cordon": True,
        "drain": {
            "force": True,
            "disableEviction": True,
            "skipWaitForDeleteTimeout": 60,
        },
        "nodeSelector": {
            "matchExpressions": [
                {
                    "key": "node-role.kubernetes.io/control-plane",
                    "operator": "DoesNotExist",
                }
            ],
        },
        "serviceAccountName": "system-upgrade",
        "prepare": {
            "image": "rancher/k3s-upgrade",
            "args": ["prepare", "server-plan"],
        },
        "upgrade": {
            "image": "rancher/k3s-upgrade",
        },
        "channel": "https://update.k3s.io/v1-release/channels/stable",
    },
    opts=ResourceOptions(depends_on=[server_plan]),
)

# Kured: reboot on update-engine signal.
kured = k8s.helm.v3.Release(
    "kured",
    k8s.helm.v3.ReleaseArgs(
        chart="kured",
        version=_chart_deps["kured"]["version"],
        namespace="kured",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["kured"]["repository"],
        ),
        values={
            "configuration": {
                # One node at a time: losing two of three control-plane nodes takes down etcd
                "concurrency": 1,
                # Abort drain after 10m, kured retries.
                "drainTimeout": "10m",
                "skipWaitForDeleteTimeout": 60,
            },
        },
        timeout=600,
    ),
)


# No auto-place on control-plane.
satellite_no_autoplace_cp = CustomResource(
    "no-autoplace-on-cp",
    api_version="piraeus.io/v1",
    kind="LinstorSatelliteConfiguration",
    metadata={"name": "no-autoplace-on-cp"},
    spec={
        "nodeAffinity": {
            "nodeSelectorTerms": [
                {
                    "matchExpressions": [
                        {
                            "key": "node-role.kubernetes.io/control-plane",
                            "operator": "Exists",
                        }
                    ]
                }
            ]
        },
        "properties": [
            {"name": "AutoplaceTarget", "value": "false"},
        ],
    },
)


# PriorityClasses for preemption.
for _pc_name, _pc_value in [
    ("app-high", "1000000"),
    ("app-medium", "100000"),
    ("app-low", "10000"),
]:
    k8s.scheduling.v1.PriorityClass(
        _pc_name,
        metadata={"name": _pc_name},
        value=int(_pc_value),
        global_default=False,
        preemption_policy="PreemptLowerPriority",
    )

# kube-vip: HA control plane and Service LoadBalancer support.
kube_vip = k8s.helm.v3.Release(
    "kube-vip",
    k8s.helm.v3.ReleaseArgs(
        chart="kube-vip",
        version=_chart_deps["kube-vip"]["version"],
        namespace="kube-system",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["kube-vip"]["repository"],
        ),
        values={
            "config": {"address": "10.10.1.99"},
            # Restrict to control-plane.
            "nodeSelector": {
                "node-role.kubernetes.io/control-plane": "true",
            },
            "tolerations": [
                {
                    "key": "node-role.kubernetes.io/control-plane",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                },
            ],
            "env": {
                "vip_interface": "eth0",
                "vip_arp": "true",
                "vip_subnet": "32",
                "cp_enable": "true",
                "svc_enable": "true",
                "svc_election": "true",
                "vip_leaderelection": "true",
                "lb_enable": "false",
                "vip_leaseduration": "15",
                "vip_renewdeadline": "10",
                "vip_retryperiod": "2",
            },
            "envValueFrom": {
                "vip_nodename": {"fieldRef": {"fieldPath": "spec.nodeName"}},
            },
            "resources": {
                "requests": {"cpu": "10m", "memory": "32Mi"},
                "limits": {"cpu": "100m", "memory": "64Mi"},
            },
        },
        timeout=600,
    ),
)


# Piraeus operator first
piraeus_operator = k8s.helm.v3.Release(
    "piraeus-operator",
    k8s.helm.v3.ReleaseArgs(
        chart="oci://ghcr.io/piraeusdatastore/piraeus-operator/piraeus",
        version=_chart_deps["piraeus"]["version"],
        namespace="piraeus-datastore",
        create_namespace=True,
        values={
            "installCRDs": True,
        },
        timeout=600,
    ),
)


# Backing store is a file-thin pool on the root disk
piraeus = k8s.helm.v3.Release(
    "linstor-cluster",
    k8s.helm.v3.ReleaseArgs(
        chart="oci://ghcr.io/piraeusdatastore/helm-charts/linstor-cluster",
        version=_chart_deps["linstor-cluster"]["version"],
        namespace="piraeus-datastore",
        create_namespace=True,
        values={
            "linstorSatelliteConfigurations": [
                {
                    "name": "storage-pool",
                    "storagePools": [
                        {
                            "name": "file-thin",
                            "fileThinPool": {
                                "directory": "/var/lib/linstor-pools/file-thin",
                            },
                        },
                    ],
                    # piraeus Flatcar how-to
                    "podTemplate": {
                        "spec": {
                            "volumes": [
                                {"name": "usr-src", "$patch": "delete"},
                            ],
                            "initContainers": [
                                {
                                    "name": "drbd-module-loader",
                                    "volumeMounts": [
                                        {
                                            "mountPath": "/usr/src",
                                            "name": "usr-src",
                                            "$patch": "delete",
                                        },
                                    ],
                                },
                            ],
                        },
                    },
                },
            ],
            "storageClasses": [
                {
                    # local-path stays the default SC; linstor is opt-in per PVC
                    "name": "linstor-r2",
                    "reclaimPolicy": "Retain",
                    "allowVolumeExpansion": True,
                    "volumeBindingMode": "WaitForFirstConsumer",
                    "provisioner": "linstor.csi.linbit.com",
                    "parameters": {
                        "linstor.csi.linbit.com/autoPlace": "2",
                        "linstor.csi.linbit.com/storagePool": "file-thin",
                        "linstor.csi.linbit.com/allowRemoteVolumeAccess": "true",
                    },
                },
            ],
        },
        timeout=600,
    ),
    opts=pulumi.ResourceOptions(depends_on=[piraeus_operator]),
)

# cert-manager issues TLS certs
cert_manager = k8s.helm.v3.Release(
    "cert-manager",
    k8s.helm.v3.ReleaseArgs(
        chart="cert-manager",
        version=_chart_deps["cert-manager"]["version"],
        namespace="cert-manager",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["cert-manager"]["repository"],
        ),
        values={
            # CRDs kept on uninstall (crds.keep defaults to true).
            "crds": {"enabled": True},
            "resources": {
                "requests": {"cpu": "10m", "memory": "64Mi"},
                "limits": {"cpu": "100m", "memory": "128Mi"},
            },
        },
        timeout=600,
    ),
)

# Cloudflare DNS-01 wildcard cert.
_tls_cfg = pulumi.Config()
_acme_email = _tls_cfg.require_secret("acmeEmail")

CF_ZONE = "lenatin.org"

# Zone-scoped Cloudflare token (Zone:DNS:Edit + Zone:Zone:Read, one zone).
cloudflare_api_token = k8s.core.v1.Secret(
    "cloudflare-api-token",
    metadata={"name": "cloudflare-api-token", "namespace": "cert-manager"},
    string_data={"api-token": _tls_cfg.require_secret("cloudflareApiToken")},
    opts=ResourceOptions(depends_on=[cert_manager]),
)


def _acme_issuer(name: str, server: str) -> CustomResource:
    """DNS-01-only issuer, locked to CF_ZONE so the token cannot solve elsewhere."""
    return CustomResource(
        name,
        api_version="cert-manager.io/v1",
        kind="ClusterIssuer",
        metadata={"name": name},
        spec={
            "acme": {
                "server": server,
                "email": _acme_email,
                "privateKeySecretRef": {"name": f"{name}-account-key"},
                "solvers": [
                    {
                        "dns01": {
                            "cloudflare": {
                                "apiTokenSecretRef": {
                                    "name": "cloudflare-api-token",
                                    "key": "api-token",
                                }
                            }
                        },
                        "selector": {"dnsZones": [CF_ZONE]},
                    }
                ],
            }
        },
        opts=ResourceOptions(depends_on=[cloudflare_api_token]),
    )


letsencrypt_dns_staging = _acme_issuer(
    "letsencrypt-dns-staging",
    "https://acme-staging-v02.api.letsencrypt.org/directory",
)
letsencrypt_dns = _acme_issuer(
    "letsencrypt-dns",
    "https://acme-v02.api.letsencrypt.org/directory",
)

# Wildcard cert in Traefik ns.
wildcard_cert = CustomResource(
    "wildcard-lenatin-org",
    api_version="cert-manager.io/v1",
    kind="Certificate",
    metadata={"name": "wildcard-lenatin-org", "namespace": "kube-system"},
    spec={
        "secretName": "wildcard-lenatin-org-tls",
        "dnsNames": [CF_ZONE, f"*.{CF_ZONE}"],
        "issuerRef": {"name": "letsencrypt-dns", "kind": "ClusterIssuer"},
        "privateKey": {"rotationPolicy": "Always"},
    },
    opts=ResourceOptions(depends_on=[letsencrypt_dns]),
)

# All Ingresses use the wildcard.
traefik_default_tls = CustomResource(
    "traefik-default-tls-store",
    api_version="traefik.io/v1alpha1",
    kind="TLSStore",
    metadata={"name": "default", "namespace": "kube-system"},
    spec={"defaultCertificate": {"secretName": "wildcard-lenatin-org-tls"}},
    opts=ResourceOptions(depends_on=[wildcard_cert]),
)

# Cloudflare origin lock-down.
_CF_ORIGIN_PULL_CA_URL = (
    "https://developers.cloudflare.com/ssl/static/authenticated_origin_pull_ca.pem"
)
_cf_ca_request = urllib.request.Request(
    _CF_ORIGIN_PULL_CA_URL, headers={"User-Agent": "curl/8"}
)
with urllib.request.urlopen(_cf_ca_request, timeout=60) as _cf_ca_response:
    CLOUDFLARE_ORIGIN_PULL_CA = _cf_ca_response.read().decode()

cloudflare_origin_pull_ca = k8s.core.v1.Secret(
    "cloudflare-origin-pull-ca",
    metadata={"name": "cloudflare-origin-pull-ca", "namespace": "kube-system"},
    string_data={"ca.crt": CLOUDFLARE_ORIGIN_PULL_CA},
)

cloudflare_origin_pull = CustomResource(
    "cloudflare-origin-pull",
    api_version="traefik.io/v1alpha1",
    kind="TLSOption",
    metadata={"name": "default", "namespace": "kube-system"},
    spec={
        "minVersion": "VersionTLS12",
        "clientAuth": {
            "secretNames": ["cloudflare-origin-pull-ca"],
            "clientAuthType": "RequireAndVerifyClientCert",
        },
    },
    opts=ResourceOptions(depends_on=[cloudflare_origin_pull_ca]),
)

# DDNS for www.lenatin.org.
ddns_namespace = k8s.core.v1.Namespace(
    "cloudflare-ddns",
    metadata={"name": "cloudflare-ddns"},
)

ddns_token = k8s.core.v1.Secret(
    "cloudflare-ddns-token",
    metadata={"name": "cloudflare-ddns-token", "namespace": "cloudflare-ddns"},
    string_data={"token": _tls_cfg.require_secret("cloudflareDdnsToken")},
    opts=ResourceOptions(depends_on=[ddns_namespace]),
)

cloudflare_ddns = k8s.apps.v1.Deployment(
    "cloudflare-ddns",
    metadata={"name": "cloudflare-ddns", "namespace": "cloudflare-ddns"},
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "cloudflare-ddns"}),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata={"labels": {"app": "cloudflare-ddns"}},
            spec=k8s.core.v1.PodSpecArgs(
                automount_service_account_token=False,
                security_context=k8s.core.v1.PodSecurityContextArgs(
                    run_as_non_root=True,
                    run_as_user=1000,
                    run_as_group=1000,
                    seccomp_profile=k8s.core.v1.SeccompProfileArgs(
                        type="RuntimeDefault"
                    ),
                ),
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="cloudflare-ddns",
                        image=(
                            "ghcr.io/favonia/cloudflare-ddns:"
                            f"{_versions['cfddns_image_tag']}"
                        ),
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="CLOUDFLARE_API_TOKEN",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name="cloudflare-ddns-token", key="token"
                                    )
                                ),
                            ),
                            {"name": "DOMAINS", "value": f"www.{CF_ZONE}"},
                            {"name": "PROXIED", "value": "true"},
                            {"name": "IP4_PROVIDER", "value": "cloudflare.trace"},
                            {"name": "IP6_PROVIDER", "value": "none"},
                            {"name": "UPDATE_CRON", "value": "@every 5m"},
                            {"name": "DELETE_ON_STOP", "value": "false"},
                        ],
                        security_context=k8s.core.v1.SecurityContextArgs(
                            allow_privilege_escalation=False,
                            read_only_root_filesystem=True,
                            capabilities=k8s.core.v1.CapabilitiesArgs(drop=["ALL"]),
                        ),
                        resources=k8s.core.v1.ResourceRequirementsArgs(
                            requests={"cpu": "10m", "memory": "32Mi"},
                            limits={"memory": "64Mi"},
                        ),
                    )
                ],
            ),
        ),
    ),
    opts=ResourceOptions(depends_on=[ddns_namespace, ddns_token]),
)

# VictoriaMetrics TSDB for KRR.
vmsingle = k8s.helm.v3.Release(
    "vmsingle",
    k8s.helm.v3.ReleaseArgs(
        chart="victoria-metrics-single",
        version=_chart_deps["victoria-metrics-single"]["version"],
        namespace="monitoring",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["victoria-metrics-single"]["repository"],
        ),
        values={
            "server": {
                "retentionPeriod": "60d",
                "extraArgs": {"promscrape.configCheckInterval": "1m"},
                # Prefer node6 (diskful replica).
                "affinity": {
                    "nodeAffinity": {
                        "preferredDuringSchedulingIgnoredDuringExecution": [
                            {
                                "weight": 100,
                                "preference": {
                                    "matchExpressions": [
                                        {
                                            "key": "kubernetes.io/hostname",
                                            "operator": "In",
                                            "values": ["node6"],
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                },
                "scrape": {
                    "enabled": True,
                    "config": {
                        "global": {"scrape_interval": "60s"},
                        "scrape_configs": [
                            {
                                "job_name": "victoriametrics",
                                "static_configs": [{"targets": ["localhost:8428"]}],
                            },
                            # kubelet cAdvisor for KRR.
                            {
                                "job_name": "kubelet",
                                "kubernetes_sd_configs": [{"role": "node"}],
                                "scheme": "https",
                                "tls_config": {
                                    "ca_file": "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
                                },
                                "bearer_token_file": "/var/run/secrets/kubernetes.io/serviceaccount/token",
                                "metrics_path": "/metrics/cadvisor",
                                "honor_timestamps": False,
                                "relabel_configs": [
                                    {
                                        "action": "labelmap",
                                        "regex": "__meta_kubernetes_node_label_(.+)",
                                    },
                                    {
                                        "source_labels": [
                                            "__meta_kubernetes_node_name"
                                        ],
                                        "target_label": "node",
                                    },
                                ],
                                "metric_relabel_configs": [
                                    {
                                        "source_labels": ["__name__"],
                                        "action": "keep",
                                        # Only what KRR actually queries
                                        "regex": (
                                            "container_cpu_usage_seconds_total"
                                            "|container_memory_working_set_bytes"
                                            "|container_network_transmit_bytes_total"
                                            "|container_network_receive_bytes_total"
                                            "|container_fs_writes_bytes_total"
                                            "|container_fs_reads_bytes_total"
                                        ),
                                    },
                                    # "POD" and empty container labels both double-count real containers
                                    {
                                        "source_labels": ["container"],
                                        "action": "drop",
                                        "regex": "|POD",
                                    },
                                ],
                            },
                            # kube-state-metrics for KRR.
                            {
                                "job_name": "kube-state-metrics",
                                "kubernetes_sd_configs": [{"role": "endpoints"}],
                                "relabel_configs": [
                                    {
                                        "source_labels": [
                                            "__meta_kubernetes_namespace",
                                            "__meta_kubernetes_service_name",
                                        ],
                                        "action": "keep",
                                        "regex": "monitoring;kube-state-metrics",
                                    },
                                    {
                                        "action": "labelmap",
                                        "regex": "__meta_kubernetes_service_label_(.+)",
                                    },
                                ],
                                "metric_relabel_configs": [
                                    {
                                        "source_labels": ["__name__"],
                                        "action": "keep",
                                        # Only what KRR actually queries
                                        "regex": (
                                            "kube_pod_info"
                                            "|kube_pod_owner"
                                            "|kube_replicaset_owner"
                                            "|kube_job_owner"
                                            "|kube_pod_status_phase"
                                            "|kube_pod_container_resource_requests"
                                            "|kube_pod_container_resource_limits"
                                            "|kube_pod_container_status_last_terminated_reason"
                                        ),
                                    },
                                ],
                            },
                        ],
                    },
                },
                "persistentVolume": {
                    "size": "8Gi",
                    "storageClassName": "linstor-r2",
                },
                "resources": {
                    "requests": {"cpu": "50m", "memory": "512Mi"},
                },
            },
        },
        # 600s for PVC binding latency.
        timeout=600,
    ),
)
# kube-state-metrics for KRR.
kube_state_metrics = k8s.helm.v3.Release(
    "kube-state-metrics",
    k8s.helm.v3.ReleaseArgs(
        chart="kube-state-metrics",
        version=_chart_deps["kube-state-metrics"]["version"],
        namespace="monitoring",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["kube-state-metrics"]["repository"],
        ),
        values={
            "fullnameOverride": "kube-state-metrics",
            "replicas": 1,
            "resources": {
                "requests": {"cpu": "10m", "memory": "64Mi"},
            },
        },
        timeout=600,
    ),
)

# Reloader: restart on ConfigMap change.
reloader = k8s.helm.v3.Release(
    "reloader",
    k8s.helm.v3.ReleaseArgs(
        chart="reloader",
        version=_chart_deps["reloader"]["version"],
        namespace="reloader",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["reloader"]["repository"],
        ),
        values={
            "reloader": {
                "reloadStrategy": "annotations",
                "reloadOnCreate": True,
                "deployment": {
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "64Mi"},
                        "limits": {"memory": "128Mi"},
                    },
                },
            },
        },
        timeout=600,
    ),
)

# Tailscale operator.
_tailscale_cfg = pulumi.Config("tailscale")

tailscale_operator = k8s.helm.v3.Release(
    "tailscale-operator",
    k8s.helm.v3.ReleaseArgs(
        chart="tailscale-operator",
        version=_chart_deps["tailscale-operator"]["version"],
        namespace="tailscale",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["tailscale-operator"]["repository"],
        ),
        values={
            "installCRDs": True,
            "oauth": {
                "clientId": _tailscale_cfg.require("clientID"),
                "clientSecret": _tailscale_cfg.require_secret("clientSecret"),
            },
            "operatorConfig": {
                "resources": {
                    "requests": {"cpu": "50m", "memory": "128Mi"},
                    "limits": {"memory": "256Mi"},
                },
            },
        },
        timeout=600,
        atomic=True,
    ),
)


# Subnet router for tailnet.
subnet_router = CustomResource(
    "subnet-router",
    api_version="tailscale.com/v1alpha1",
    kind="Connector",
    metadata={"name": "subnet-router"},
    spec={
        "replicas": 1,
        "hostnamePrefix": "subnet-router",
        "subnetRouter": {"advertiseRoutes": ["10.10.1.0/24"]},
    },
    opts=ResourceOptions(depends_on=[tailscale_operator]),
)
