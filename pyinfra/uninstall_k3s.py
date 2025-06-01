"""
This script will uninstall k3s from the host.
It checks if the k3s-uninstall.sh script exists and then executes it.
"""

from pyinfra import host

K3S_UNINSTALL_SCRIPT = "/opt/bin/k3s-uninstall.sh"

if host.get_fact(Block, K3S_UNINSTALL_SCRIPT):
    uninstall_result = server.shell(
        name=f"Uninstall k3s on {host.name}", commands=[f"{K3S_UNINSTALL_SCRIPT}"]
    )
else:
    print(f"k3s is already uninstalled on {host.name}")
