from pyinfra import host
from pyinfra.facts.files import Block
from pyinfra.operations import python, server

# Check if k3s is installed by looking for the uninstall script
K3S_UNINSTALL_SCRIPT = "/opt/bin/k3s-uninstall.sh"

if not host.get_fact(Block, K3S_UNINSTALL_SCRIPT):
    # Your code here...
    uninstall_result = server.shell(
        name=f"Uninstall k3s on {host.name}",
        commands=[f"{K3S_UNINSTALL_SCRIPT}"]
    )


def callback():
    print(f"Uninstall output for {host.name}:")
    print(f"Got result: {uninstall_result.stdout}")


python.call(
    name="Execute callback function",
    function=callback,
)