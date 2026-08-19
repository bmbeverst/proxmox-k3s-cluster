# Proxmox User + Password Auth for Pulumi

How to create the Proxmox VE user + password that Pulumi uses 
Run the PVE commands on the **host** as `root@pam`

## 1. Create the Pulumi user (optional if it already exists)

```bash
pveum user add pulumi@pve --comment "Pulumi Proxmox provider"
```

## 2. Set a password for that user

```bash
pveum passwd pulumi@pve
```

Prompts for the password twice.

## 3. Create the least-privilege role

Mirrors the built-in `PVEVMUser` plus the datastore / SDN rights needed to
linked-clone a VM and attach it to a network.

```bash
pveum role add Pulumi-VM --privs \
  "VM.Allocate VM.Audit VM.Backup VM.Clone VM.Console VM.Migrate \
   VM.Monitor VM.PowerMgmt VM.Snapshot VM.Config.CDROM VM.Config.CPU \
   VM.Config.Cloudinit VM.Config.Disk VM.Config.HWType VM.Config.Memory \
   VM.Config.Network VM.Config.Options Datastore.AllocateSpace Datastore.Allocate \
   Datastore.Audit SDN.Use"

# If the role already exists, update it instead:
# pveum role modify Pulumi-VM --privs "<same privilege list as above>"
```

## 4. Grant the role to the user — VMs and storage

The script writes to two datastores, so grant the role on the VM paths and the
storage paths.

```bash
# VM paths (template 9002 + the nodes it creates)
pveum acl modify /vms -user pulumi@pve -role Pulumi-VM

# Datastore: clone target 'data' (disk + cloud-init drive)
pveum acl modify /storage/data -user pulumi@pve -role Pulumi-VM

# Datastore: hook script snippet lives on 'data_dir'
pveum acl modify /storage/data_dir -user pulumi@pve -role Pulumi-VM
```

> Simpler alternative (whole cluster, still capability-restricted):
> ```bash
> pveum acl modify / -user pulumi@pve -role Pulumi-VM
> ```

## 5. Store the credentials (your workstation -> Pulumi)

From `pulumi/proxmox/`:

```bash
pulumi config set proxmoxve:endpoint https://pvehostname:8006/
pulumi config set --secret proxmoxve:username "pulumi@pve"
pulumi config set --secret proxmoxve:password "<password from step 2>"
pulumi config set proxmoxve:insecure false
```

## Rotate the password later

```bash
pveum passwd pulumi@pve
# then on your workstation:
pulumi config set --secret proxmoxve:password "<new password>"
```
