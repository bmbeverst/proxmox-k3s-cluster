from ansible_vault import Vault

# Common configuration for all hosts
ssh_user = "core"

# Non-k3s hosts lack these keys; install_k3s.py refuses to run.

tls_san = "10.10.1.99"
# kube-vip VIP join target; only join flags/checks use it.
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


# Secrets live in the encrypted vault, not here.
vault_data = readVault()

# Get k3s token from vault
k3s_token = vault_data.get("k3s_token")
if not k3s_token:
    raise Exception("k3s_token not found in vault")
