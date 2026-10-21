from ansible_vault import Vault

# Common configuration for all hosts
ssh_user = "core"

# k3s_role and proxmox_host are set per k3s group, so a host outside those groups
# (e.g. the Proxmox hosts themselves) has neither key and install_k3s.py refuses
# to run on it. See group_data/k3s_servers.py and group_data/k3s_agents.py.

tls_san = "10.10.1.99"
# Join target is the kube-vip VIP: only the server/agent join flags and the
# reachability check use it, so no persisted --server/K3S_URL bakes in a node IP.
init_node_ip = "10.10.1.99"


def readVault():
    file_path = ".vault_pass"
    vault_path = "./group_data/k3s/vault"
    try:
        with open(file_path, "r") as f:
            vault_pass = f.read().strip()
    except FileNotFoundError:
        raise Exception(f"Error: File '{file_path}' not found.")
    vault = Vault(vault_pass)

    with open(vault_path) as fp:
        vault_data = vault.load(fp.read())

    return vault_data


# Secrets live in the encrypted vault (group_data/k3s/vault), never in plain text here.
vault_data = readVault()

# Get k3s token from vault
k3s_token = vault_data.get("k3s_token")
if not k3s_token:
    raise Exception("k3s_token not found in vault")
