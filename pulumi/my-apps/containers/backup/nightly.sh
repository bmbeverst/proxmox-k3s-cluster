#!/usr/bin/env sh
# Stop the server, back up the world, start the server again.
set -euo pipefail

NAMESPACE="${NAMESPACE:-vintagestory}"
DEPLOYMENT="${DEPLOYMENT:-vints}"
POD_SELECTOR="${POD_SELECTOR:-app=vints}"
STOP_TIMEOUT="${STOP_TIMEOUT:-180s}"
SA_TOKEN="${SA_TOKEN:-/var/run/secrets/kubernetes.io/serviceaccount/token}"

scale() {
  kubectl -n "$NAMESPACE" scale "deployment/$DEPLOYMENT" --replicas="$1"
}

# No service account token means no cluster (CI smoke test): just back up.
if [ ! -f "$SA_TOKEN" ]; then
  echo "[nightly] no in-cluster config: backing up the running server"
  exec /bin/sh /backup.sh
fi

echo "[nightly] stopping $NAMESPACE/$DEPLOYMENT for a quiesced backup"
scale 0

# Wait for the server to be gone, or the tar catches a half-written world.
if [ -n "$(kubectl -n "$NAMESPACE" get pods -l "$POD_SELECTOR" -o name)" ]; then
  kubectl -n "$NAMESPACE" wait --for=delete pod -l "$POD_SELECTOR" --timeout="$STOP_TIMEOUT"
fi
sync

echo "[nightly] backing up the stopped server"
rc=0
/bin/sh /backup.sh || rc=$?

# The server comes back even if the backup failed.
echo "[nightly] starting $NAMESPACE/$DEPLOYMENT again (daily reboot)"
scale 1
# No waiting for Ready: we hold the RWO volume.
echo "[nightly] done (backup rc=$rc)"
exit "$rc"

