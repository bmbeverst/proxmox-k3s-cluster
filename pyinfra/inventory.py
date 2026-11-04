# etcd members: two on pve, one on pve2.
k3s_servers = [
    ("10.10.1.41", {"init_k3s": True}),
    ("10.10.1.42", {}),
    ("10.10.1.44", {"proxmox_host": "pve2"}),
]

# Workers: node5 heavy on pve, node6 light on pve2.
k3s_agents = [
    ("10.10.1.45", {}),
    ("10.10.1.46", {"proxmox_host": "pve2"}),
]
