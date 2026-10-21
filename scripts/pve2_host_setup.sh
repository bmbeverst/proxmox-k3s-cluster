#!/usr/bin/env bash
# Re-apply the pve2 host state that a reinstall destroys.
#
#   ./scripts/pve2_host_setup.sh [root@10.10.1.22]
#
# Three things, each idempotent and checked against the live host first:
#   1. e1000e hardware-hang workaround (EEE + TSO/GSO/GRO/SG off on the physical NIC)
#   2. swap/IO sysctl tuning; swap itself is never created, resized or removed
#   3. ZFS ARC cap: 1.55 GiB, because pve2 runs ~12 GiB of VMs in 15.5 GiB of RAM
#
# Deliberately not pyinfra: pyinfra owns the k3s nodes only. This drives the
# hypervisor over root ssh and is safe to re-run.
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

# The physical NIC is whatever vmbr0 bridges to. The name is not stable across
# installs (nic0 here, enp0s25/enp1s0 elsewhere), so read it, never hardcode it.
nic=$(awk '/^iface vmbr0/{f=1} f && /bridge-ports/{print $2; exit}' /etc/network/interfaces)
if [ -z "${nic:-}" ]; then
    echo "error: no bridge-ports found for vmbr0 in /etc/network/interfaces" >&2
    exit 1
fi
echo "   nic: $nic"

# EEE off alone did not stop the hangs (5,385 in the 13 h it ran alone); the
# offloads are the part that did. Both go in one retry loop because the link
# may still be coming up when this runs.
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
echo "-- swap/IO sysctl tuning (swap is never created or resized)"
remote bash -s <<'REMOTE'
set -euo pipefail

# Report only: whether pve2 has swap is whatever the installer decided. This
# script does not create, resize or remove it.
if swapon --show --noheadings | grep -q .; then
    swapon --show | sed 's/^/   /'
else
    echo "   no swap active (fine, nothing is created)"
fi

# Swap as a safety valve, and HDD writeback that starts early instead of in one
# burst at the dirty_ratio cap. Do not add vm.dirty_bytes here: it resets
# dirty_ratio to 0 and the cap silently moves to 20% of RAM.
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
# pve2 runs ~12 GiB of VMs in 15.5 GiB of RAM, so the default ARC (50%) is far too big.
options zfs zfs_arc_max=${ARC_MAX_BYTES}
CONF

cur=$(cat /sys/module/zfs/parameters/zfs_arc_max)
echo "   zfs_arc_max now: $cur"
if [ "$cur" != "$ARC_MAX_BYTES" ]; then
    # Runtime lowering works; raising needs the module option plus a reboot.
    if echo "$ARC_MAX_BYTES" >/sys/module/zfs/parameters/zfs_arc_max 2>/dev/null; then
        echo "   lowered at runtime to $ARC_MAX_BYTES"
    else
        echo "   runtime set refused; the modprobe option applies on the next boot"
    fi
fi
REMOTE

echo "done"
