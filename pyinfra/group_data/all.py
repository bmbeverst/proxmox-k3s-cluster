from ansible_vault import Vault

# Common configuration for all hosts
ssh_user = 'core'

tls_san = "10.10.1.99"
# Must match the init node IP in the inventory
init_node_ip = "10.10.1.51"

def readVaultToken():
    file_path = '.vault_pass'
    vault_path = "./group_data/k3s/vault"
    try:
        with open(file_path, 'r') as f:
            vault_pass =  f.read().strip()
    except FileNotFoundError:
        raise Exception(f"Error: File '{file_path}' not found.")
    vault = Vault(vault_pass)

    with open(vault_path) as fp:
        vault_data = vault.load(fp.read())
     
    return vault_data.get('k3s_token')

# Get k3s token from vault
k3s_token = readVaultToken()
if not k3s_token:
    raise Exception("k3s_token not found in vault")