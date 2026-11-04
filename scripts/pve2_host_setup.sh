#!/usr/bin/env bash
# Re-apply pve2 host state lost on reinstall. Idempotent, safe to re-run.
#   ./scripts/pve2_host_setup.sh [root@10.10.1.22]
#   1. e1000e workaround: EEE + TSO/GSO/GRO/SG off on the physical NIC
#   2. swap/IO sysctl tuning (never creates, resizes or removes swap)
#   3. ZFS ARC cap: 1.55 GiB (pve2 runs ~12 GiB VMs in 15.5 GiB RAM)
# Not pyinfra: drives the hypervisor over root ssh.
set -euo pipefail

TARGET="${1:-root@10.10.1.22}"
ARC_MAX_BYTES=1667235840

remote() { ssh -o BatchMode=yes "$TARGET" "$@"; }

echo "== $TARGET"
if ! remote 'command -v qm >/dev/null'; then
    echo "error: $TARGET is not reachable or is not a Proxmox host (no qm)" >&2
    exit 1
fi

# --------------------------------------------------------------------- NIC
echo "-- e1000e hang workaround"
remote bash -s <<'REMOTE'
set -euo pipefail

# NIC name varies across installs; read from vmbr0 bridge-ports.
nic=$(awk '/^iface vmbr0/{f=1} f && /bridge-ports/{print $2; exit}' /etc/network/interfaces)
if [ -z "${nic:-}" ]; then
    echo "error: no bridge-ports found for vmbr0 in /etc/network/interfaces" >&2
    exit 1
fi
echo "   nic: $nic"

# EEE alone insufficient; offloads too; single retry loop.
cat >/etc/systemd/system/e1000e-eee-off.service <<UNIT
[Unit]
Description=e1000e hardware-hang workaround: disable EEE + TSO/GSO/GRO/SG offloads on $nic (Intel I217-LM)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'for i in \$(seq 1 30); do /usr/sbin/ethtool --set-eee $nic advertise 0 && /usr/sbin/ethtool -K $nic tso off gso off gro off sg off && exit 0; sleep 5; done; exit 1'

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable e1000e-eee-off.service
systemctl restart e1000e-eee-off.service
if ! systemctl is-active --quiet e1000e-eee-off.service; then
    echo "error: e1000e-eee-off.service did not come up (journalctl -u e1000e-eee-off)" >&2
    exit 1
fi
ethtool -k "$nic" | grep -E '^(scatter-gather|tcp-segmentation-offload|generic-receive-offload):' | sed 's/^/   /'
REMOTE

# -------------------------------------------------------------------- swap
echo "-- swap/IO sysctl tuning + swap zvol (swap is created once, never removed)"
remote bash -s <<'REMOTE'
set -euo pipefail

# Swap zvol on rpool is only a safety valve.
if ! swapon --show --noheadings | grep -q .; then
    echo "-- creating 8 GiB swap zvol on rpool"
    zfs create -V 8G -o compression=off -o logbias=latency -o sync=standard rpool/swap
    mkswap -f /dev/zvol/rpool/swap
    swapon /dev/zvol/rpool/swap
    echo "/dev/zvol/rpool/swap none swap sw 0 0" >> /etc/fstab
fi

echo "swap:"
swapon --show | sed 's/^/   /'

# Swap safety valve plus HDD writeback; avoid vm.dirty_bytes.
cat >/etc/sysctl.d/99-vm-tuning.conf <<CONF
vm.swappiness = 1
vm.dirty_background_bytes = 268435456
vm.dirty_ratio = 20
CONF
sysctl --system >/dev/null
REMOTE

# --------------------------------------------------------------------- ARC
echo "-- ZFS ARC cap (${ARC_MAX_BYTES} bytes)"
remote "ARC_MAX_BYTES=${ARC_MAX_BYTES}" bash -s <<'REMOTE'
set -euo pipefail

if [ ! -d /sys/module/zfs ]; then
    echo "error: zfs module not loaded, is this a ZFS install?" >&2
    exit 1
fi

cat >/etc/modprobe.d/zfs.conf <<CONF
# pve2 VMs leave too little RAM for default ARC.
options zfs zfs_arc_max=${ARC_MAX_BYTES}
CONF

cur=$(cat /sys/module/zfs/parameters/zfs_arc_max)
echo "   zfs_arc_max now: $cur"
if [ "$cur" != "$ARC_MAX_BYTES" ]; then
    # Runtime lowering works; raising needs a reboot.
    if echo "$ARC_MAX_BYTES" >/sys/module/zfs/parameters/zfs_arc_max 2>/dev/null; then
        echo "   lowered at runtime to $ARC_MAX_BYTES"
    else
        echo "   runtime set refused; the modprobe option applies on the next boot"
    fi
fi
REMOTE

echo "done"
