# Servers are the etcd members: two on pve, one on pve2. Group defaults live in
# group_data/k3s_servers.py, so a host only lists what it overrides.
k3s_servers = [
    ("10.10.1.111", {"init_k3s": True}),
    ("10.10.1.112", {}),
    ("10.10.1.44", {"proxmox_host": "pve2"}),
]

# Workers, one per host: node5 heavy on pve, node6 light on pve2.
# Group defaults (agent role, pve) live in group_data/k3s_agents.py.
k3s_agents = [
    ("10.10.1.45", {}),
    ("10.10.1.46", {"proxmox_host": "pve2"}),
]
