"""
This script will install k3s on a host and configure journald to not use so much memory.
It will first install on the init node,
then checks that kuberentes is running and then installs on all other nodes.
Skips the k3s install if already installed.
"""

from pyinfra import host
from pyinfra.facts.files import Directory
from pyinfra.operations import files, python, server, systemd
from pyinfra.operations.util import any_changed

# Get TLS SAN from host info
tls_san = host.data.get("tls_san")

k3s_token = host.data.get("k3s_token")

# Check if this is the init node
is_init_node = host.data.get("init_k3s", False)


def wait_for_init_node(init_node_ip):
    # Wait for init node to be reachable with retries
    for attempt in range(3):
        result = server.shell(
            f'/usr/bin/curl --connect-timeout 10 --silent --show-error "{init_node_ip}":6443'
        )
        if result.did_succeed():
            break
        if attempt == 5:  # Last attempt
            print(f"Error: Could not connect to init node at {init_node_ip}:6443")
            print(f"Last error: {stderr}")
            raise Exception(f"Failed to connect to init node after 3 attempts")


# Check if k3s is already installed
if host.get_fact(Directory, "/etc/rancher/k3s/"):
    print("k3s is already installed")
else:
    if is_init_node:
        # Install k3s on init node
        server.shell(
            name="Install k3s on init node",
            commands=[
                f'curl -sfL https://get.k3s.io | sh -s - --secrets-encryption --token "{k3s_token}" --tls-san "{tls_san}" --cluster-init'  # pylint: disable=line-too-long
            ],
        )

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

        python.call(
            name="Wait for init node to respond",
            function=wait_for_init_node,
            init_node_ip=init_node_ip,
        )
        # Install k3s on worker node
        server.shell(
            name="Install k3s on worker node",
            commands=[
                f'curl -sfL https://get.k3s.io | sh -s - --secrets-encryption --token "{k3s_token}" --tls-san "{tls_san}" --server "https://{init_node_ip}:6443"'  # pylint: disable=line-too-long
            ],
            # _timeout=120, # This can be an issue due to GitHub throttling
        )

# Configure journald
sys_max = files.line(
    name="Set SystemMaxUse in journald.conf",
    path="/etc/systemd/journald.conf",
    line="SystemMaxUse=256M    # Maximum total journal size",
)
run_max = files.line(
    name="Set RuntimeMaxUse in journald.conf",
    path="/etc/systemd/journald.conf",
    line="RuntimeMaxUse=128M   # Maximum journal size in temporary storage",
)
file_max = files.line(
    name="Set MaxFileSec in journald.conf",
    path="/etc/systemd/journald.conf",
    line="MaxFileSec=1month    # Maximum time to retain log files",
)


# Restart journald if any configuration was changed
systemd.service(
    "systemd-journald",
    running=True,
    restarted=True,
    _if=any_changed(sys_max, run_max, file_max),
)
