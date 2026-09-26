#!/usr/bin/env bash
# Daily CronJob: snapshot the world (Saves/ + serverconfig.json), tar it, and write it
# to an NFS share (atomic rename on the share). Retains the 7 newest backups.
# Members mirror $DATA_PATH, so a restore is one command: tar -xzf saves-<ts>.tar.gz -C /data
# Runs under /bin/sh (busybox ash in the image): no bash arrays/bashisms.
set -euo pipefail

DATA_PATH="${DATA_PATH:-/data}"
BACKUP_DEST="${BACKUP_DEST:?BACKUP_DEST required}"   # mount path of the NFS share
SNAP="$DATA_PATH/.backup-snap"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -d "$DATA_PATH/Saves" ]]; then
  echo "[backup] no Saves dir yet; nothing to back up"
  exit 0
fi

# Hard-link snapshot of the world (cheap: same filesystem as Saves).
rm -rf "$SNAP"
mkdir -p "$SNAP/Saves"
cp -al "$DATA_PATH/Saves/." "$SNAP/Saves/"

# serverconfig.json pins "SaveFileLocation" (an absolute path) + the world settings.
members="Saves"
if [[ -f "$DATA_PATH/serverconfig.json" ]]; then
  cp -a "$DATA_PATH/serverconfig.json" "$SNAP/serverconfig.json"
  members="$members serverconfig.json"
fi

tmp="$BACKUP_DEST/saves-$TS.tar.gz.part"
final="$BACKUP_DEST/saves-$TS.tar.gz"

echo "[backup] taring $members -> $final"
# Deliberate word splitting: $members is a space-separated tar member list.
# shellcheck disable=SC2086
tar -C "$SNAP" -czf "$tmp" $members
rm -rf "$SNAP"
# Atomic on the NFS share: the tar never appears under its final name until complete.
mv -f "$tmp" "$final"

echo "[backup] pruning to newest 7"
ls -1t "$BACKUP_DEST"/saves-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r -I{} rm -f "{}"
echo "[backup] done"