#!/usr/bin/env bash
# Daily CronJob: hard-link snapshot of the world Saves, tar it, and write it to an
# NFS share (atomic rename on the share). Retains the 7 newest backups.
set -euo pipefail

DATA_PATH="${DATA_PATH:-/data}"
BACKUP_DEST="${BACKUP_DEST:?BACKUP_DEST required}"   # mount path of the NFS share
SNAP="$DATA_PATH/.backup-snap"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -d "$DATA_PATH/Saves" ]]; then
  echo "[backup] no Saves dir yet; nothing to back up"
  exit 0
fi

# Atomic point-in-time snapshot via hard links (same filesystem as Saves).
rm -rf "$SNAP"
mkdir -p "$SNAP"
cp -al "$DATA_PATH/Saves/." "$SNAP/"

tmp="$BACKUP_DEST/saves-$TS.tar.gz.part"
final="$BACKUP_DEST/saves-$TS.tar.gz"

echo "[backup] taring Saves -> $final"
tar -C "$SNAP" -czf "$tmp" .
rm -rf "$SNAP"
# Atomic on the NFS share: the tar never appears under its final name until complete.
mv -f "$tmp" "$final"

echo "[backup] pruning to newest 7"
ls -1t "$BACKUP_DEST"/saves-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r -I{} rm -f "{}"
echo "[backup] done"