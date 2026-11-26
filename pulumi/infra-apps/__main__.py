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
    # Only touch the Deployment; leave other resources alone.
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

# LB IP pool (range-global); avoids VIPs .60/.90/.91/.99.
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
        "postCompleteDelay": "300s",
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
        # The job must run on the tainted control-plane node it upgrades.
        "tolerations": [
            {
                "key": "node-role.kubernetes.io/control-plane",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        ],
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
        "postCompleteDelay": "300s",
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
            # Must be declared: the live DaemonSet tolerates these, but the chart default is [].
            # Without them (e.g. after a chart bump re-renders this release) kured stops covering
            # node1/2/4 and staged Flatcar updates there would never be rebooted.
            "tolerations": [
                {
                    "key": "node-role.kubernetes.io/control-plane",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                },
                {
                    "key": "node-role.kubernetes.io/master",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                },
            ],
            "extraArgs": {
                # Reboot only if Flatcar update pending AND DRBD healthy.
                "reboot-sentinel-command": (
                    "sh -c 'test -f /var/run/reboot-required -o -f /sentinel/reboot-required || exit 1; "
                    # pidof+cmdline: pgrep -f matches kured's own command line.
                    "P=$(for p in $(pidof java); do "
                    "grep -qa com.linbit.linstor.core.Satellite /proc/$p/cmdline "
                    "&& echo $p && break; done); "
                    '[ -n "$P" ] || exit 1; '
                    "O=$(nsenter -m/proc/$P/ns/mnt -n/proc/$P/ns/net -- "
                    "/usr/sbin/drbdadm status 2>/dev/null) || exit 1; "
                    'printf %s "$O" | grep -qE "connection:(StandAlone|Connecting'
                    '|Disconnecting|WFConnection)|disk:(Inconsistent|Outdated)" && exit 1; '
                    "exit 0'"
                ),
            },
            "configuration": {
                # One node at a time (etcd quorum).
                "concurrency": 1,
                # Spacing: period caps reboot gap; lockReleaseDelay adds node grace.
                "period": "2h",
                "lockReleaseDelay": "30m",
                # Abort drain after 10m, kured retries.
                "drainTimeout": "10m",
                "skipWaitForDeleteTimeout": 60,
            },
        },
        timeout=600,
    ),
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
                # API VIP only. Service VIPs belong to the worker instance (kube_vip_lb):
                # with this left true the CP wins the per-service leases, which loses client
                # IPs and breaks externalTrafficPolicy: Local services (vints) on a CP reboot.
                "svc_enable": "false",
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

# Worker kube-vip: per-service election, real client IPs.
kube_vip_lb = k8s.helm.v3.Release(
    "kube-vip-lb",
    k8s.helm.v3.ReleaseArgs(
        chart="kube-vip",
        version=_chart_deps["kube-vip"]["version"],
        namespace="kube-system",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["kube-vip"]["repository"],
        ),
        values={
            # nameOverride keeps DS/SA/RBAC names off "kube-vip".
            "nameOverride": "kube-vip-lb",
            # Workers only.
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "node-role.kubernetes.io/control-plane",
                                        "operator": "DoesNotExist",
                                    }
                                ]
                            }
                        ]
                    }
                }
            },
            "env": {
                "vip_interface": "eth0",
                "vip_arp": "true",
                "vip_subnet": "32",
                "cp_enable": "false",
                "svc_enable": "true",
                "svc_election": "true",
                "vip_leaderelection": "false",
                "lb_enable": "false",
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


# k3s ships traefik as a packaged HelmChart; this merges extra values into it.
traefik_config = CustomResource(
    "traefik-chart-config",
    api_version="helm.cattle.io/v1",
    kind="HelmChartConfig",
    metadata={"name": "traefik", "namespace": "kube-system"},
    spec={
        "valuesContent": """deployment:
  replicas: 2
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: node-role.kubernetes.io/control-plane
              operator: Exists
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: traefik
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    memory: 256Mi
""",
    },
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


# System components run on the control-plane: workers stay free for app pods.
CONTROL_PLANE_AFFINITY = {
    "nodeAffinity": {
        "requiredDuringSchedulingIgnoredDuringExecution": {
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
        }
    }
}

# Pod tolerations are patched atomically, so repeat the operator's defaults.
CONTROL_PLANE_TOLERATIONS = [
    {
        "key": "node-role.kubernetes.io/control-plane",
        "operator": "Exists",
        "effect": "NoSchedule",
    },
    {
        "key": "node.kubernetes.io/not-ready",
        "operator": "Exists",
        "effect": "NoExecute",
        "tolerationSeconds": 30,
    },
    {
        "key": "node.kubernetes.io/unreachable",
        "operator": "Exists",
        "effect": "NoExecute",
        "tolerationSeconds": 30,
    },
    {
        "key": "drbd.linbit.com/force-io-error",
        "operator": "Exists",
        "effect": "NoSchedule",
    },
    {
        "key": "drbd.linbit.com/lost-quorum",
        "operator": "Exists",
        "effect": "NoSchedule",
    },
]


# Backing store: file-thin pool on the root disk.
piraeus = k8s.helm.v3.Release(
    "linstor-cluster",
    k8s.helm.v3.ReleaseArgs(
        chart="oci://ghcr.io/piraeusdatastore/helm-charts/linstor-cluster",
        version=_chart_deps["linstor-cluster"]["version"],
        namespace="piraeus-datastore",
        create_namespace=True,
        values={
            # LINSTOR brain was BestEffort; no limit (-Xmx8G).
            "linstorCluster": {
                # Satellites must run on every node: each volume's third quorum vote (a diskless
                # replica) lives on a control plane today, and LINSTOR may place one anywhere.
                # Declared explicitly so a future re-render can't drop it and silently remove
                # the tiebreakers from the control planes.
                "tolerations": [{"operator": "Exists"}],
                "controller": {
                    "podTemplate": {
                        "spec": {
                            "affinity": CONTROL_PLANE_AFFINITY,
                            "tolerations": CONTROL_PLANE_TOLERATIONS,
                            "containers": [
                                {
                                    "name": "linstor-controller",
                                    "resources": {
                                        "requests": {
                                            "cpu": "500m",
                                            "memory": "1Gi",
                                        },
                                    },
                                },
                            ],
                        },
                    },
                },
                # Sidecars elect a leader, so two replicas survive one CP loss.
                "csiController": {
                    "replicas": 2,
                    "podTemplate": {
                        "spec": {
                            "affinity": CONTROL_PLANE_AFFINITY,
                            "tolerations": CONTROL_PLANE_TOLERATIONS,
                            "containers": [
                                {
                                    "name": "linstor-csi",
                                    "resources": {
                                        "requests": {"cpu": "50m", "memory": "64Mi"},
                                        "limits": {"memory": "256Mi"},
                                    },
                                },
                                {
                                    "name": "csi-attacher",
                                    "resources": {
                                        "requests": {"cpu": "10m", "memory": "32Mi"},
                                        "limits": {"memory": "128Mi"},
                                    },
                                },
                                {
                                    "name": "csi-provisioner",
                                    "resources": {
                                        "requests": {"cpu": "10m", "memory": "32Mi"},
                                        "limits": {"memory": "128Mi"},
                                    },
                                },
                                {
                                    "name": "csi-resizer",
                                    "resources": {
                                        "requests": {"cpu": "10m", "memory": "32Mi"},
                                        "limits": {"memory": "128Mi"},
                                    },
                                },
                                {
                                    "name": "csi-snapshotter",
                                    "resources": {
                                        "requests": {"cpu": "10m", "memory": "32Mi"},
                                        "limits": {"memory": "128Mi"},
                                    },
                                },
                                {
                                    "name": "csi-livenessprobe",
                                    "resources": {
                                        "requests": {"cpu": "10m", "memory": "32Mi"},
                                        "limits": {"memory": "64Mi"},
                                    },
                                },
                            ],
                        },
                    },
                },
            },
            "linstorSatelliteConfigurations": [
                # Every node runs the same satellite pod, control-plane included:
                # the CP nodes are the diskless quorum tiebreakers.
                {
                    "name": "satellite-pod",
                    "podTemplate": {
                        "spec": {
                            "hostNetwork": True,
                            "dnsPolicy": "ClusterFirstWithHostNet",
                            "containers": [
                                {
                                    "name": "linstor-satellite",
                                    "resources": {
                                        "requests": {
                                            "cpu": "100m",
                                            "memory": "512Mi",
                                        },
                                        "limits": {"memory": "2560Mi"},
                                    },
                                },
                            ],
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
                # Workers hold the data: with no pool on the control-plane, the
                # autoplacer can only put a diskless tiebreaker there.
                {
                    "name": "storage-pool",
                    "nodeAffinity": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "node-role.kubernetes.io/control-plane",
                                        "operator": "DoesNotExist",
                                    }
                                ]
                            }
                        ]
                    },
                    "storagePools": [
                        {
                            "name": "file-thin",
                            "fileThinPool": {
                                "directory": "/var/lib/linstor-pools/file-thin",
                            },
                        },
                    ],
                },
            ],
            "storageClasses": [
                {
                    # local-path stays default SC; linstor is opt-in per PVC.
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

snapshot_controller = k8s.helm.v3.Release(
    "snapshot-controller",
    k8s.helm.v3.ReleaseArgs(
        chart="oci://ghcr.io/piraeusdatastore/helm-charts/snapshot-controller",
        version=_chart_deps["snapshot-controller"]["version"],
        namespace="snapshot-controller",
        create_namespace=True,
        values={
            "installCRDs": True,
            "keepCRDs": True,
            "controller": {
                "affinity": CONTROL_PLANE_AFFINITY,
                "tolerations": CONTROL_PLANE_TOLERATIONS,
                "resources": {
                    "requests": {"cpu": "10m", "memory": "32Mi"},
                    "limits": {"memory": "128Mi"},
                },
            },
        },
        timeout=600,
    ),
)

# Own resource (chart validates the VSC before its CRD); Retain keeps the snapshot.
# driver/deletionPolicy are top-level fields: VolumeSnapshotClass has no spec.
linstor_snapshot_class = CustomResource(
    "linstor-snap",
    api_version="snapshot.storage.k8s.io/v1",
    kind="VolumeSnapshotClass",
    metadata={"name": "linstor-snap"},
    driver="linstor.csi.linbit.com",
    deletionPolicy="Retain",
    opts=ResourceOptions(depends_on=[snapshot_controller]),
)

# VolSync: restic backups of app PVCs to the OMV NFS export.
volsync = k8s.helm.v3.Release(
    "volsync",
    k8s.helm.v3.ReleaseArgs(
        chart="volsync",
        version=_chart_deps["volsync"]["version"],
        namespace="volsync-system",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["volsync"]["repository"],
        ),
        values={
            "manageCRDs": True,
            "metrics": {"disableAuth": True},
            "affinity": CONTROL_PLANE_AFFINITY,
            "tolerations": CONTROL_PLANE_TOLERATIONS,
            "resources": {
                "requests": {"cpu": "50m", "memory": "128Mi"},
                "limits": {"memory": "256Mi"},
            },
        },
        timeout=600,
    ),
    opts=ResourceOptions(depends_on=[snapshot_controller, linstor_snapshot_class]),
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

# The Mosquitto cert (my-apps) only carries DNS:mqtt.lenatin.org, and that name
# has no public record because DNS-01 issuance does not need one. Pin it to the
# broker's LAN VIP so in-cluster clients can verify the cert by hostname; every
# other name in the zone still resolves via the default block. k3s imports
# /etc/coredns/custom/*.server as top-level server blocks.
MOSQUITTO_TLS_HOST = "mqtt.lenatin.org"
MOSQUITTO_LB_IP = "10.10.1.64"  # owned by my-apps/homeassistant.py
COREDNS_MQTT_SERVER = f"""\
{MOSQUITTO_TLS_HOST}:53 {{
    errors
    hosts {{
        {MOSQUITTO_LB_IP} {MOSQUITTO_TLS_HOST}
        fallthrough
    }}
    forward . /etc/resolv.conf
}}
"""
coredns_custom = k8s.core.v1.ConfigMap(
    "coredns-custom",
    metadata={"name": "coredns-custom", "namespace": "kube-system"},
    data={"mqtt.server": COREDNS_MQTT_SERVER},
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
                    "requests": {"cpu": "100m", "memory": "768Mi"},
                    "limits": {"memory": "1Gi"},
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

# KEDA: event-driven autoscaling, including to/from zero.
keda = k8s.helm.v3.Release(
    "keda",
    k8s.helm.v3.ReleaseArgs(
        chart="keda",
        version=_chart_deps["keda"]["version"],
        namespace="keda",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["keda"]["repository"],
        ),
        values={
            # CRDs come from git; the admission webhook only validates/defaults them.
            "webhooks": {"enabled": False},
            "resources": {
                "operator": {
                    "requests": {"cpu": "50m", "memory": "100Mi"},
                    "limits": {"memory": "256Mi"},
                },
                "metricServer": {
                    "requests": {"cpu": "50m", "memory": "100Mi"},
                    "limits": {"memory": "256Mi"},
                },
                "webhooks": {
                    "requests": {"cpu": "10m", "memory": "64Mi"},
                    "limits": {"memory": "128Mi"},
                },
            },
        },
        timeout=600,
    ),
)

# KEDA HTTP add-on: request-driven scale-to-zero; the interceptor takes the request.
keda_http = k8s.helm.v3.Release(
    "keda-add-ons-http",
    k8s.helm.v3.ReleaseArgs(
        chart="keda-add-ons-http",
        version=_chart_deps["keda-add-ons-http"]["version"],
        namespace="keda",
        create_namespace=True,
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo=_chart_deps["keda-add-ons-http"]["repository"],
        ),
        values={
            "operator": {
                "resources": {
                    "requests": {"cpu": "10m", "memory": "64Mi"},
                    "limits": {"memory": "128Mi"},
                },
            },
            # One app, one request path: 3 scaler replicas are wasted.
            "scaler": {
                "replicas": 1,
                "resources": {
                    "requests": {"cpu": "10m", "memory": "64Mi"},
                    "limits": {"memory": "128Mi"},
                },
            },
            # Interceptor is the HTTP entry point for scale-from-zero. One replica is enough here,
            # and with min=1 its PDB allows a drain to evict it instead of stalling.
            "interceptor": {
                "replicas": {"min": 1, "max": 5},
                "resources": {
                    "requests": {"cpu": "10m", "memory": "64Mi"},
                    "limits": {"memory": "192Mi"},
                },
            },
        },
        timeout=600,
    ),
    opts=ResourceOptions(depends_on=[keda]),
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
