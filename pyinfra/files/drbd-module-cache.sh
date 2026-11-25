#!/bin/sh
# Cache the DRBD 9 build for the running kernel.
#
# piraeus's drbd-module-loader compiles DRBD 9 from source on every boot (~2 min on
# the workers) because Flatcar ships DRBD 8.4.11 and piraeus needs >= 9. That build is
# discarded with the satellite pod. This copies it into /var/lib/drbd-modules/current
# so the next boot on the same kernel loads it instantly, via
# /etc/modprobe.d/zz-drbd-cache.conf and /etc/modules-load.d/drbd.conf.
#
# No-op when the cache already matches `uname -r`, so it is safe to run on a timer.
set -eu

base=/var/lib/drbd-modules
cur="$base/current"
k=$(uname -r)

if [ -f "$cur/KERNEL" ] && [ "$(cat "$cur/KERNEL")" = "built-for: $k" ]; then
    exit 0
fi

# Newest build output from the satellite's drbd-module-loader init container.
src=$(ls -t /var/lib/kubelet/pods/*/volumes/kubernetes.io~empty-dir/tmp/ko/drbd.ko 2>/dev/null | head -1)
[ -n "$src" ] || exit 0

# Only take it if it was built for the kernel we are running now.
vermagic=$(/sbin/modinfo -F vermagic "$src" 2>/dev/null | awk '{print $1}')
[ "$vermagic" = "$k" ] || exit 0

srcdir=$(dirname "$src")
mkdir -p "$cur"
for f in drbd.ko drbd_transport_tcp.ko drbd_transport_lb-tcp.ko drbd_transport_rdma.ko; do
    [ -f "$srcdir/$f" ] || continue
    cp -f "$srcdir/$f" "$cur/$(printf '%s' "$f" | tr '-' '_')"
    chmod 644 "$cur/$(printf '%s' "$f" | tr '-' '_')"
done
# Written last: an interrupted copy leaves a stale marker and gets retried.
printf 'built-for: %s\n' "$k" > "$cur/KERNEL"
echo "cached DRBD 9 build for $k from $srcdir"
