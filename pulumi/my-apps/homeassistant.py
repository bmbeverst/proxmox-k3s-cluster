"""Home Assistant + Mosquitto, own `home-assistant` namespace.

- Home Assistant runs one replica on hostNetwork so mDNS discovery (ESPHome,
  HomeKit, Chromecast) works; `Recreate` keeps two pods off the config volume.
- Mosquitto is a LAN-only broker; its password file is rebuilt from Pulumi
  secrets at every start, since the upstream image has no env-based auth.
- Both keep state on linstor-r2 PVCs.
"""

import os

import pulumi
import pulumi_kubernetes as k8s
import yaml
from pulumi_kubernetes.meta.v1 import ObjectMetaArgs

with open(os.path.join(os.path.dirname(__file__), "..", "versions.yaml")) as f:
    _versions = yaml.safe_load(f)

# Read from repo; changes ship via git.
with open(
    os.path.join(os.path.dirname(__file__), "pod-scripts", "mosquitto-start.sh")
) as f:
    MOSQUITTO_START_SCRIPT = f.read()

HOME_ASSISTANT_IMAGE = (
    f"homeassistant/home-assistant:{_versions['homeassistant_image_tag']}"
)
MOSQUITTO_IMAGE = f"eclipse-mosquitto:{_versions['mosquitto_image_tag']}"

NAMESPACE = "home-assistant"

HOME_ASSISTANT_PORT = 8123
MOSQUITTO_PORT = 1883

# kube-vip LB VIPs, LAN only; .64/.65 are free in the .61-.80 pool.
MOSQUITTO_LB_IP = "10.10.1.64"
HOME_ASSISTANT_LB_IP = "10.10.1.65"

HOME_ASSISTANT_DATA_SIZE = "5Gi"
MOSQUITTO_DATA_SIZE = "1Gi"

# 2.1 moved auth into a plugin; the password file is rebuilt at every start.
MOSQUITTO_CONFIG = """\
listener 1883
allow_anonymous false
plugin /usr/lib/mosquitto_password_file.so
plugin_opt_password_file /mosquitto/data/passwd
persistence true
persistence_location /mosquitto/data/
log_dest stdout
"""

_cfg = pulumi.Config("my-apps")
_mosquitto_user = _cfg.require("mosquittoUser")
_mosquitto_password = _cfg.require_secret("mosquittoPassword")


def register(namespace):
    """Create the Mosquitto and Home Assistant workloads, PVCs and Services."""

    mosquitto_auth = k8s.core.v1.Secret(
        "mosquitto-auth",
        metadata=ObjectMetaArgs(name="mosquitto-auth", namespace=NAMESPACE),
        string_data={
            "username": _mosquitto_user,
            "password": _mosquitto_password,
        },
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    mosquitto_config = k8s.core.v1.ConfigMap(
        "mosquitto-config",
        metadata=ObjectMetaArgs(name="mosquitto-config", namespace=NAMESPACE),
        data={"mosquitto.conf": MOSQUITTO_CONFIG},
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    mosquitto_start = k8s.core.v1.ConfigMap(
        "mosquitto-start",
        metadata=ObjectMetaArgs(name="mosquitto-start", namespace=NAMESPACE),
        data={"mosquitto-start.sh": MOSQUITTO_START_SCRIPT},
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # Retained messages and broker state.
    mosquitto_storage = k8s.core.v1.PersistentVolumeClaim(
        "mosquitto-data",
        metadata=ObjectMetaArgs(name="mosquitto-data", namespace=NAMESPACE),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],
            storage_class_name="linstor-r2",
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                requests={"storage": MOSQUITTO_DATA_SIZE},
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # RWO: Recreate so two brokers never share the data volume.
    mosquitto = k8s.apps.v1.Deployment(
        "mosquitto",
        metadata=ObjectMetaArgs(
            name="mosquitto",
            namespace=NAMESPACE,
            annotations={"reloader.stakater.com/auto": "true"},
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            strategy=k8s.apps.v1.DeploymentStrategyArgs(type="Recreate"),
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "mosquitto"}),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(
                    labels={"app": "mosquitto"},
                    # Placeholder so the nested ignore below can resolve.
                    annotations={"reloader.stakater.com/last-reloaded-from": "unset"},
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    priority_class_name="app-high",
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="mosquitto",
                            image=MOSQUITTO_IMAGE,
                            image_pull_policy="IfNotPresent",
                            # Wrapper builds the password file, then execs the broker.
                            command=["/bin/sh", "/scripts/mosquitto-start.sh"],
                            env=[
                                k8s.core.v1.EnvVarArgs(
                                    name="MQTT_USER",
                                    value_from=k8s.core.v1.EnvVarSourceArgs(
                                        secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                            name="mosquitto-auth", key="username"
                                        )
                                    ),
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="MQTT_PASSWORD",
                                    value_from=k8s.core.v1.EnvVarSourceArgs(
                                        secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                            name="mosquitto-auth", key="password"
                                        )
                                    ),
                                ),
                            ],
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="mqtt",
                                    container_port=MOSQUITTO_PORT,
                                ),
                            ],
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "10m", "memory": "32Mi"},
                                limits={"memory": "128Mi"},
                            ),
                            # TCP only; the broker closes unauthenticated CONNECTs.
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                tcp_socket=k8s.core.v1.TCPSocketActionArgs(
                                    port=MOSQUITTO_PORT
                                ),
                                initial_delay_seconds=5,
                                period_seconds=10,
                                failure_threshold=3,
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                tcp_socket=k8s.core.v1.TCPSocketActionArgs(
                                    port=MOSQUITTO_PORT
                                ),
                                initial_delay_seconds=10,
                                period_seconds=30,
                                failure_threshold=3,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="mosquitto-data", mount_path="/mosquitto/data"
                                ),
                                k8s.core.v1.VolumeMountArgs(
                                    name="mosquitto-config",
                                    mount_path="/mosquitto/config",
                                    read_only=True,
                                ),
                                k8s.core.v1.VolumeMountArgs(
                                    name="mosquitto-start",
                                    mount_path="/scripts",
                                    read_only=True,
                                ),
                            ],
                        ),
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="mosquitto-data",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="mosquitto-data"
                            ),
                        ),
                        k8s.core.v1.VolumeArgs(
                            name="mosquitto-config",
                            config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                                name="mosquitto-config"
                            ),
                        ),
                        k8s.core.v1.VolumeArgs(
                            name="mosquitto-start",
                            config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                                name="mosquitto-start"
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(
            depends_on=[
                namespace,
                mosquitto_auth,
                mosquitto_config,
                mosquitto_start,
                mosquitto_storage,
            ],
            ignore_changes=[
                'spec.template.metadata.annotations["reloader.stakater.com/last-reloaded-from"]',
            ],
        ),
    )

    # In-cluster endpoint for Home Assistant.
    mosquitto_service = k8s.core.v1.Service(
        "mosquitto",
        metadata=ObjectMetaArgs(name="mosquitto", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "mosquitto"},
            type="ClusterIP",
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="mqtt",
                    port=MOSQUITTO_PORT,
                    target_port=MOSQUITTO_PORT,
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, mosquitto]),
    )

    # TouchKio and the Pi connect here; no TLS, so LAN only.
    mosquitto_lb = k8s.core.v1.Service(
        "mosquitto-lb",
        metadata=ObjectMetaArgs(name="mosquitto-lb", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "mosquitto"},
            type="LoadBalancer",
            load_balancer_ip=MOSQUITTO_LB_IP,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="mqtt",
                    port=MOSQUITTO_PORT,
                    target_port=MOSQUITTO_PORT,
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, mosquitto]),
    )

    # Onboarding writes configuration.yaml; never seed it from a ConfigMap.
    home_assistant_storage = k8s.core.v1.PersistentVolumeClaim(
        "home-assistant-config",
        metadata=ObjectMetaArgs(name="home-assistant-config", namespace=NAMESPACE),
        spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
            access_modes=["ReadWriteOnce"],
            storage_class_name="linstor-r2",
            resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                requests={"storage": HOME_ASSISTANT_DATA_SIZE},
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace]),
    )

    # hostNetwork: mDNS discovery needs LAN multicast; Recreate: one writer.
    home_assistant = k8s.apps.v1.Deployment(
        "home-assistant",
        metadata=ObjectMetaArgs(name="home-assistant", namespace=NAMESPACE),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            strategy=k8s.apps.v1.DeploymentStrategyArgs(type="Recreate"),
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels={"app": "home-assistant"}
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=ObjectMetaArgs(labels={"app": "home-assistant"}),
                spec=k8s.core.v1.PodSpecArgs(
                    priority_class_name="app-high",
                    host_network=True,
                    # hostNetwork pods otherwise lose cluster DNS.
                    dns_policy="ClusterFirstWithHostNet",
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="home-assistant",
                            image=HOME_ASSISTANT_IMAGE,
                            image_pull_policy="IfNotPresent",
                            env=[{"name": "TZ", "value": "America/New_York"}],
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="http",
                                    container_port=HOME_ASSISTANT_PORT,
                                ),
                            ],
                            resources=k8s.core.v1.ResourceRequirementsArgs(
                                requests={"cpu": "100m", "memory": "512Mi"},
                                # No CPU limit: startup and updates spike.
                                limits={"memory": "1Gi"},
                            ),
                            # Landing page is unauthenticated; first boot is slow.
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/",
                                    port=HOME_ASSISTANT_PORT,
                                ),
                                initial_delay_seconds=30,
                                period_seconds=15,
                                timeout_seconds=5,
                                failure_threshold=3,
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/",
                                    port=HOME_ASSISTANT_PORT,
                                ),
                                initial_delay_seconds=120,
                                period_seconds=30,
                                timeout_seconds=5,
                                failure_threshold=3,
                            ),
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="home-assistant-config",
                                    mount_path="/config",
                                ),
                            ],
                        ),
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="home-assistant-config",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name="home-assistant-config"
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, home_assistant_storage]),
    )

    # Port 80 on the VIP keeps the browsed URL plain.
    home_assistant_service = k8s.core.v1.Service(
        "home-assistant",
        metadata=ObjectMetaArgs(name="home-assistant", namespace=NAMESPACE),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector={"app": "home-assistant"},
            type="LoadBalancer",
            load_balancer_ip=HOME_ASSISTANT_LB_IP,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=80,
                    target_port=HOME_ASSISTANT_PORT,
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[namespace, home_assistant]),
    )
