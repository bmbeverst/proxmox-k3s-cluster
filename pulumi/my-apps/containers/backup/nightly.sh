#!/usr/bin/env sh
# Nightly maintenance: stop the game, back it up while nothing is writing, start it again.
# Run by the vints-backup CronJob (00:00 America/New_York) with a ServiceAccount that may
# only scale this one Deployment and watch its pods.
#
# Why stop it at all: a running server is a moving target (the world is a WAL database the
# server keeps writing to), so a point-in-time archive means a point-in-time world. The
# restart doubles as the daily reboot, so the downtime buys something.
#
# This pod mounts the same PVC on the same node as the game pod (podAffinity), so the
# volume stays attached to the node when the game pod goes away: scaling to zero does not
# take the data away from us.
set -euo pipefail

NAMESPACE="${NAMESPACE:-vintagestory}"
DEPLOYMENT="${DEPLOYMENT:-vints}"
POD_SELECTOR="${POD_SELECTOR:-app=vints}"
STOP_TIMEOUT="${STOP_TIMEOUT:-180s}"

scale() {
  kubectl -n "$NAMESPACE" scale "deployment/$DEPLOYMENT" --replicas="$1"
}

echo "[nightly] stopping $NAMESPACE/$DEPLOYMENT for a quiesced backup"
scale 0

# Wait for the server process to be gone before touching the data: tarring a server that
# is still shutting down captures a half-written world. `kubectl wait` errors when its
# selector matches nothing, so skip it if the pod is already gone.
if [ -n "$(kubectl -n "$NAMESPACE" get pods -l "$POD_SELECTOR" -o name)" ]; then
  kubectl -n "$NAMESPACE" wait --for=delete pod -l "$POD_SELECTOR" --timeout="$STOP_TIMEOUT"
fi
# Flush whatever the node still holds in page cache before the snapshot.
sync

echo "[nightly] backing up the stopped server"
rc=0
/bin/sh /backup.sh || rc=$?

# Whatever the backup did, the server goes back up: a failed tar must not leave the game
# down. A non-zero rc still fails the Job, so the run is visible.
echo "[nightly] starting $NAMESPACE/$DEPLOYMENT again (daily reboot)"
scale 1
# Deliberately not waiting for Ready here: this pod holds the RWO volume, and if the fresh
# pod lands on the other replica node it cannot attach until we exit. Log and go.
echo "[nightly] done (backup rc=$rc)"
exit "$rc"
