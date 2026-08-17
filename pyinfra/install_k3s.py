"""
This script will install k3s on a host and configure journald to not use so much memory.
It will first install on the init node,
then checks that kuberentes is running and then installs on all other nodes.
Skips the k3s install if already installed.
"""

from pyinfra import host
from pyinfra.facts.files import Directory
from pyinfra.operations import files, server, systemd

# Get TLS SAN from host info
tls_san = host.data.get("tls_san")

k3s_token = host.data.get("k3s_token")

# Check if this is the init node
is_init_node = host.data.get("init_k3s", False)


# Check if k3s is already installed
if host.get_fact(Directory, "/etc/rancher/k3s/"):
    print("k3s is already installed")
else:
    if is_init_node:
        # Install k3s on init node
        server.shell(
            name="Install k3s on init node",
            commands=[
                f'curl -sfL https://get.k3s.io | INSTALL_K3S_SKIP_SELINUX_RPM=true sh -s - --secrets-encryption --token "{k3s_token}" --tls-san "{tls_san}" --cluster-init'  # pylint: disable=line-too-long
            ],
        )

        files.get(
            name="Got k3s.yaml, change 'server:' and move to ~/.kube/config",
            src="/etc/rancher/k3s/k3s.yaml",  # Path on the remote host
            dest="k3s.yaml",  # Path on your local machine
            _sudo=True,
        )
        print("Copied k3s config to local machine at k3s.yaml")

        # Wait for k3s to be ready on init node
        # TODO fix that this executes after the curl commands, which is not correct
        # server.shell(
        #     name="Wait for k3s to be ready on init node",
        #     commands=[
        #         'until systemctl is-active k3s; do sleep 1; done'
        #     ],
        # )
    else:
        # Get init node IP
        init_node_ip = host.data.get("init_node_ip")

        # Robust wait: poll https://<init>:6443 up to ~90s, exiting non-zero if it
        # never comes up (so pyinfra genuinely fails instead of silently passing).
        server.shell(
            name="Wait for init node k3s API to be reachable",
            commands=[
                f'for i in $(seq 1 15); do '
                f'if curl -sk --connect-timeout 2 -o /dev/null "https://{init_node_ip}:6443"; then '
                f'exit 0; fi; sleep 2; done; '
                f'echo "init node {init_node_ip}:6443 not reachable after ~30s" >&2; exit 1'
            ],
        )
        # Install k3s on worker node
        server.shell(
            name="Install k3s on worker node",
            commands=[
                f'curl -sfL https://get.k3s.io | INSTALL_K3S_SKIP_SELINUX_RPM=true sh -s - --secrets-encryption --token "{k3s_token}" --tls-san "{tls_san}" --server "https://{init_node_ip}:6443"'  # pylint: disable=line-too-long
            ],
            # _timeout=120, # This can be an issue due to GitHub throttling
        )

k3s_config = files.put(
    name="Set image-gc-threshold in k3s/config.yaml",
    src="files/k3s_config.yaml",
    dest="/etc/rancher/k3s/config.yaml",
    mode="644",
    user="root",
    group="root",
    _sudo=True,
)


# Restart k3s if any configuration was changed
systemd.service(
    "k3s",
    running=True,
    restarted=True,
    _if=k3s_config.did_change,
    _sudo=True,
)

# Configure journald via a drop-in
journald_dropin = files.put(
    name="Set journald size caps via drop-in",
    src="files/journald-caps.conf",
    dest="/etc/systemd/journald.conf.d/10-size-caps.conf",
    mode="644",
    user="root",
    group="root",
    _sudo=True,
)


# Restart journald if the drop-in changed
systemd.service(
    "systemd-journald",
    running=True,
    restarted=True,
    _if=journald_dropin.did_change,
    _sudo=True,
)

# Set REBOOT_STRATEGY=off to stage OS updates, let kured coordinate the reboot.
files.line(
    name="Set REBOOT_STRATEGY=off so kured coordinates reboots",
    path="/etc/flatcar/update.conf",
    line=r"^REBOOT_STRATEGY=.*",
    replace="REBOOT_STRATEGY=off",
    _sudo=True,
)
