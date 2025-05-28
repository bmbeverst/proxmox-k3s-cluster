

from pyinfra import host
from pyinfra.facts.files import Directory
from pyinfra.operations import files, server, systemd, python
from pyinfra.operations.util import any_changed

# Check if k3s is already installed
if host.get_fact(Directory, "/etc/rancher/k3s/k3s.yaml"):
    raise Exception("k3s is already installed")

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
        if attempt == 2:  # Last attempt
            print(f"Error: Could not connect to init node at {init_node_ip}:6443")
            print(f"Last error: {stderr}")
            raise Exception(f"Failed to connect to init node after 3 attempts")
    


if is_init_node:
    # Install k3s on init node
    server.shell(
        name="Install k3s on init node",
        commands=[
            f'curl -sfL https://get.k3s.io | sh -s - --secrets-encryption --token "{k3s_token}" --tls-san "{tls_san}" --cluster-init'
        ],
    )
    
    # Wait for k3s to be ready on init node
    server.shell(
        name="Wait for k3s to be ready on init node",
        commands=[
            'until systemctl is-active k3s; do sleep 1; done'
        ],
    )
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
            f'curl -sfL https://get.k3s.io | sh -s - --secrets-encryption --token "{k3s_token}" --tls-san "{tls_san}" --server https://"{init_node_ip}":6443'
        ],
    )

# Configure journald
journald_changes = [
    files.line(
        name="Set SystemMaxUse in journald.conf",
        path="/etc/systemd/journald.conf",
        line="SystemMaxUse=256M    # Maximum total journal size",
    ),
    files.line(
        name="Set RuntimeMaxUse in journald.conf",
        path="/etc/systemd/journald.conf",
        line="RuntimeMaxUse=128M   # Maximum journal size in temporary storage",
    ),
    files.line(
        name="Set MaxFileSec in journald.conf",
        path="/etc/systemd/journald.conf",
        line="MaxFileSec=1month    # Maximum time to retain log files",
    ),
]

# Restart journald if any configuration was changed
systemd.service(
    "systemd-journald",
    running=True,
    restarted=True,
    _if=any_changed(journald_changes),
)