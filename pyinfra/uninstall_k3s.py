"""
This script will uninstall k3s from the host.
It checks if the uninstall script for the host's role exists and then executes it.
"""

from pyinfra import host
from pyinfra.facts.files import File
from pyinfra.operations import server

k3s_role = host.data.get("k3s_role")
if not k3s_role:
    raise Exception("k3s_role must be set (group_data/all.py)")

# Servers install k3s-uninstall.sh, agents k3s-agent-uninstall.sh (Flatcar: /opt/bin).
K3S_UNINSTALL_SCRIPT = (
    "/opt/bin/k3s-agent-uninstall.sh"
    if k3s_role == "agent"
    else "/opt/bin/k3s-uninstall.sh"
)

if host.get_fact(File, path=K3S_UNINSTALL_SCRIPT):
    uninstall_result = server.shell(
        name=f"Uninstall k3s on {host.name}", commands=[f"{K3S_UNINSTALL_SCRIPT}"]
    )
else:
    print(f"k3s is already uninstalled on {host.name}")
