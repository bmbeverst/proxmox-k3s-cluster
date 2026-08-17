from typing import TypedDict

import os
import pathlib

import pulumi
import pulumi_proxmoxve as proxmoxve


class NodeConfig(TypedDict):
    name: str
    vm_id: int
    ip: str


# ---------------------------------------------------------------------------
# Configuration — change values here, everything else follows.
# ---------------------------------------------------------------------------
NODES: list[NodeConfig] = [
    {"name": "node1", "vm_id": 111, "ip": "10.10.1.111/24"},
    {"name": "node2", "vm_id": 112, "ip": "10.10.1.112/24"},
    {"name": "node3", "vm_id": 113, "ip": "10.10.1.113/24"},
]

TEMPLATE_VM_ID = 9002
NODE_NAME = "pve"
DATASTORE = "data"
# Hook script lives in the `data_dir` datastore (snippets).
HOOK_SCRIPT = "data_dir:snippets/hook-fcar.sh"
# Flatcar config base dir used by the -fw_cfg ignition argument (host path).
FLATCAR_IGN_BASE = "/etc/pve/flatcar"
LINKED_CLONE = True
ON_BOOT = False

# Hardware / network defaults.
CORES = 3
MEMORY_MB = 7168   # 7 GiB; no floating = no balloon (non-ballooning)
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

    vm = proxmoxve.vm.VirtualMachine(
        name,
        node_name=NODE_NAME,
        vm_id=vm_id,
        name=name,
        clone={
            "vm_id": TEMPLATE_VM_ID,
            "node_name": NODE_NAME,
            "full": not LINKED_CLONE,
            # `datastore_id` is only valid for FULL clones. PVE refuses it for
            # linked clones, which inherit the template's datastore (9002 is
            # on `data`), so we omit it when LINKED_CLONE is True.
            **({"datastore_id": DATASTORE} if not LINKED_CLONE else {}),
        },
        agent={
            "enabled": True,
            "trim": True,  # fstrim_cloned_disks
        },
        cpu={
            "type": "host",
            "cores": CORES,
        },
        memory={
            "dedicated": MEMORY_MB,
        },
        operating_system={
            "type": "l26",
        },
        scsi_hardware="virtio-scsi-single",
        disks=[
            {
                "interface": "scsi0",
                "datastore_id": DATASTORE,
                "iothread": True,
                "cache": "none",
                "aio": "io_uring",
                "discard": "ignore",
                "ssd": False,
                "size": 24,
            },
        ],
        kvm_arguments=(
            f"-fw_cfg name=opt/org.flatcar-linux/config,"
            f"file={FLATCAR_IGN_BASE}/{vm_id}.ign"
        ),
        hook_script_file_id=HOOK_SCRIPT,
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
            "datastore_id": DATASTORE,
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
            ignore_changes=["agent", "cdrom", "disks", "serialDevices", "vga", "started",
                             "macAddresses", "networkInterfaceNames", "ipv4Addresses", "ipv6Addresses"]
        ),
    )
