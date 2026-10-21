import os
import pathlib
from typing import NotRequired, TypedDict

import pulumi
import pulumi_proxmoxve as proxmoxve


class NodeConfig(TypedDict):
    """One VM. Anything not set falls back to the DEFAULT_* values below."""

    name: str
    vm_id: int
    ip: str
    node_name: NotRequired[str]
    datastore: NotRequired[str]
    cores: NotRequired[int]
    memory_mb: NotRequired[int]
    disk_gb: NotRequired[int]
    cache: NotRequired[str]
    full_clone: NotRequired[bool]


# ---------------------------------------------------------------------------
# Configuration — change values here, everything else follows.
# ---------------------------------------------------------------------------
NODES: list[NodeConfig] = [
    # Two servers on pve, one on pve2 (s1/s2/s3). node3 is still running and is
    # retired only after node4 has rejoined: adding first, removing second keeps
    # etcd at 3+ members the whole way.
    {"name": "node1", "vm_id": 111, "ip": "10.10.1.111/24"},
    {"name": "node2", "vm_id": 112, "ip": "10.10.1.112/24"},
    {"name": "node3", "vm_id": 113, "ip": "10.10.1.113/24"},
    # s3: server on pve2. The template only exists on pve, so a cross-node clone
    # has to be full — which is also what pvesr needs to replicate the disk.
    # cache=none is not negotiable here: pve2 is HDD-backed and this VM is an
    # etcd member, so `unsafe` (the old manual setting) can lose quorum writes.
    {
        "name": "node4",
        "vm_id": 102,
        "ip": "10.10.1.44/24",
        "node_name": "pve2",
        "cache": "none",
        "full_clone": True,
    },
    # a1: heavy worker on pve, gets the RAM/CPU the two servers leave.
    {
        "name": "node5",
        "vm_id": 115,
        "ip": "10.10.1.45/24",
        "cores": 8,
        "memory_mb": 14336,
        "disk_gb": 40,
        "full_clone": True,
    },
    # a2: light worker on pve2.
    {
        "name": "node6",
        "vm_id": 116,
        "ip": "10.10.1.46/24",
        "node_name": "pve2",
        "disk_gb": 40,
        "cache": "none",
        "full_clone": True,
    },
]

TEMPLATE_VM_ID = 9002
# Where the template lives; VMs may be cloned to another node_name.
TEMPLATE_NODE = "pve"
ON_BOOT = False

# Per-VM defaults: a control-plane VM on pve, cloned (not copied) from TEMPLATE_NODE.
DEFAULT_NODE_NAME = "pve"
DEFAULT_DATASTORE = "data"
DEFAULT_CORES = 2
DEFAULT_MEMORY_MB = 6144  # 6 GiB for a server; no floating = no balloon
DEFAULT_DISK_GB = 24
# PVE's default for a VM disk. Never `unsafe`: it ignores the guest's flushes, so
# an etcd write it acknowledges can be lost on a host power cut.
DEFAULT_DISK_CACHE = "none"
DEFAULT_FULL_CLONE = False

# Network defaults.
BRIDGE = "vmbr0"
GATEWAY = "10.10.1.1"

# Username baked into the template (Flatcar default is `core`).
CLOUDINIT_USER = "core"
# SSH public key is read from this path at run time.
SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_rsa.pub")


def _read_ssh_key() -> str:
    try:
        return pathlib.Path(SSH_KEY_PATH).read_text().strip()
    except OSError as exc:
        raise RuntimeError(
            f"Could not read SSH public key from {SSH_KEY_PATH!r}: {exc}"
        ) from exc


ssh_public_key = _read_ssh_key()

for node in NODES:
    name = node["name"]
    vm_id = node["vm_id"]
    ip = node["ip"]
    node_name = node.get("node_name", DEFAULT_NODE_NAME)
    datastore = node.get("datastore", DEFAULT_DATASTORE)
    cores = node.get("cores", DEFAULT_CORES)
    memory_mb = node.get("memory_mb", DEFAULT_MEMORY_MB)
    disk_gb = node.get("disk_gb", DEFAULT_DISK_GB)
    disk_cache = node.get("cache", DEFAULT_DISK_CACHE)
    full_clone = node.get("full_clone", DEFAULT_FULL_CLONE)

    vm = proxmoxve.vm.VirtualMachine(
        name,
        node_name=node_name,
        vm_id=vm_id,
        name=name,
        clone={
            "vm_id": TEMPLATE_VM_ID,
            "node_name": TEMPLATE_NODE,
            "full": full_clone,
            # `datastore_id` is only valid for FULL clones. PVE refuses it for
            # linked clones, which inherit the template's datastore (9002 is
            # on `data`), so we omit it for linked clones.
            **({"datastore_id": datastore} if full_clone else {}),
        },
        agent={
            "enabled": True,
            "trim": True,  # fstrim_cloned_disks
        },
        cpu={
            "type": "host",
            "cores": cores,
        },
        memory={
            "dedicated": memory_mb,
        },
        operating_system={
            "type": "l26",
        },
        scsi_hardware="virtio-scsi-single",
        disks=[
            {
                "interface": "scsi0",
                "datastore_id": datastore,
                "iothread": True,
                "cache": disk_cache,
                "aio": "io_uring",
                "discard": "ignore",
                "ssd": False,
                "size": disk_gb,
            },
        ],
        # hookscript and args (the Flatcar -fw_cfg ignition pointer) are
        # deliberately absent: PVE only lets root@pam set them, and this stack
        # auths as the least-privilege pulumi@pve user (PROXMOX-USER-AUTH.md).
        # Both arrive without root anyway: clones inherit the template 9002's
        # hookscript, and hook-fcar.sh sets the fw_cfg args at the VM's first
        # pre-start. Both are in ignore_changes so node1-3's state-held values
        # (set in a root context, pre least-privilege user) are never purged.
        network_devices=[
            {
                "bridge": BRIDGE,
                "model": "virtio",
            }
        ],
        serial_devices=[
            {
                "device": "socket",
            }
        ],
        vga={
            "type": "serial0",
        },
        tablet_device=False,
        on_boot=ON_BOOT,
        initialization={
            "datastore_id": datastore,
            "type": "nocloud",
            "ip_configs": [
                {
                    "ipv4": {
                        "address": ip,
                        "gateway": GATEWAY,
                    },
                }
            ],
            "user_account": {
                "username": CLOUDINIT_USER,
                "keys": [ssh_public_key],
            },
        },
        # Ignore harmless current-state drift so updates don't try to "fix" it.
        opts=pulumi.ResourceOptions(
            ignore_changes=[
                "agent",
                "cdrom",
                "disks",
                "serialDevices",
                "vga",
                "started",
                "macAddresses",
                "networkInterfaceNames",
                "ipv4Addresses",
                "ipv6Addresses",
                # Root-only fields (see the comment above network_devices):
                # present in state for node1-3, absent from code forever.
                "hookScriptFileId",
                "kvmArguments",
            ]
        ),
    )
