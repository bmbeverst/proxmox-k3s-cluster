import pulumi_proxmoxve as proxmoxve

import pulumi

template_id="TMPL-flatcar-current"

# Create three nodes based on the template
node1 = proxmoxve.vm.VirtualMachine(
    "node1",
    node_name="pve",
    vm_id=105,
    clone={
        "vm_id": 9000,
        "full": True,
        "node_name": "pve"
    },
    memory={
        "dedicated": 4096,
        "balloon": 5120
    },
    agent={
        "enabled": True
    },
    kvm_arguments="-fw_cfg name=opt/org.flatcar-linux/config,file=/etc/pve/flatcar/105.ign",
    cpu={
        "type": "host",
        "cores": 2
    },
    hook_script_file_id="data_dir:snippets/hook-fcar.sh",
    cdrom={
        "enabled": True,
        "media": "cdrom",
        "volume": "data_dir:105/vm-105-cloudinit.qcow2"
    },
    network_devices=[
        {
            "bridge": "vmbr0",
            "model": "virtio",
        }
    ],
    initialization ={
        "ip_configs": [{
                "ipv4": {
                    "ip": "10.10.1.51/24",
                    "gateway": "10.10.1.1"
                }
            }],
        "datastore_id": "data_dir"
    },
    on_boot=True,
    disks=[
        {
            "interface": "scsi0",
            "size": 16886,
            "storage": "data_dir",
            "volume": "105/vm-105-disk-0.qcow2"
        }
    ],
)

node2 = proxmoxve.vm.VirtualMachine(
    "node2",
    node_name="pve",
    vm_id=106,
    clone={
        "vm_id": 9000,
        "full": True,
        "node_name": "pve"
    },
    memory={
        "dedicated": 4096,
        "balloon": 5120
    },
    agent={
        "enabled": True
    },
    kvm_arguments="-fw_cfg name=opt/org.flatcar-linux/config,file=/etc/pve/flatcar/106.ign",
    cpu={
        "type": "host",
        "cores": 2
    },
    hook_script_file_id="data_dir:snippets/hook-fcar.sh",
    cdrom={
        "enabled": True,
        "media": "cdrom",
        "volume": "data_dir:106/vm-106-cloudinit.qcow2"
    },
    network_devices=[
        {
            "bridge": "vmbr0",
            "model": "virtio",
        }
    ],
    initialization ={
        "ip_configs": [{
            "ipv4": {
                "ip": "10.10.1.52/24",
            "gateway": "10.10.1.1"
            }
        }],
        "datastore_id": "data_dir"
    },
    on_boot=True,
    disks=[
        {
            "interface": "scsi0",
            "size": 16886,
            "storage": "data_dir",
            "volume": "106/vm-106-disk-0.qcow2"
        }
    ],
)

node3 = proxmoxve.vm.VirtualMachine(
    "node3",
    node_name="pve",
    vm_id=107,
    clone={
        "vm_id": 9000,
        "full": True,
        "node_name": "pve"
    },
    memory={
        "dedicated": 4096,
        "balloon": 5120
    },
    agent={
        "enabled": True
    },
    kvm_arguments="-fw_cfg name=opt/org.flatcar-linux/config,file=/etc/pve/flatcar/107.ign",
    cpu={
        "type": "host",
        "cores": 2
    },
    hook_script_file_id="data_dir:snippets/hook-fcar.sh",
    cdrom={
        "enabled": True,
        "media": "cdrom",
        "volume": "data_dir:107/vm-107-cloudinit.qcow2"
    },
    network_devices=[
        {
            "bridge": "vmbr0",
            "model": "virtio",
        }
    ],
    initialization ={
        "ip_configs": [{
                "ipv4": {
                    "ip": "10.10.1.53/24",
                    "gateway": "10.10.1.1"
                }
            }],
        "datastore_id": "data_dir"
    },
    on_boot=True,
    disks=[
        {
            "interface": "scsi0",
            "size": 16886,
            "storage": "data_dir",
            "volume": "107/vm-107-disk-0.qcow2"
        }
    ],
)

pulumi.export("node1_public_ip", node1.networks[0].ip_address)
pulumi.export("node2_public_ip", node2.networks[0].ip_address)
pulumi.export("node3_public_ip", node3.networks[0].ip_address)