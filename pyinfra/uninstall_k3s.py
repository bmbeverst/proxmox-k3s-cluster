from pyinfra import host
from pyinfra.operations import server

# Check if k3s is installed by looking for the uninstall script
K3S_UNINSTALL_SCRIPT = "/opt/bin/k3s-uninstall.sh"

# Loop through all servers
for server in host.data.get("servers", []):
    print(f"Processing server: {server}")

    # Check if k3s is installed
    result = host.run_shell(f"ls {K3S_UNINSTALL_SCRIPT}")

    if result.rc == 2:  # File not found
        print(f"k3s is already uninstalled on {server}")
        continue

    # If we get here, k3s is installed, so proceed with uninstallation
    uninstall_result = server.shell(
        name=f"Uninstall k3s on {server}",
        commands=[f"{K3S_UNINSTALL_SCRIPT}"]
    )

    # Log the output
    print(f"Uninstall output for {server}:")
    print(uninstall_result.stdout)
