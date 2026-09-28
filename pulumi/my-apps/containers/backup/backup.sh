#!/usr/bin/env bash
# Tar the world + config to the NFS share, keep the 7 newest backups.
# Members mirror $DATA_PATH: restore with tar -xzf saves-<ts>.tar.gz -C /data
# Runs under /bin/sh (busybox ash): no bash arrays.
set -euo pipefail

DATA_PATH="${DATA_PATH:-/data}"
BACKUP_DEST="${BACKUP_DEST:?BACKUP_DEST required}"   # mount path of the NFS share
SNAP="$DATA_PATH/.backup-snap"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

# Sweep stale .part files: the prune below only matches finished archives.
rm -f "$BACKUP_DEST"/saves-*.tar.gz.part

if [[ ! -d "$DATA_PATH/Saves" ]]; then
  echo "[backup] no Saves dir yet; nothing to back up"
  exit 0
fi

# Copy the world first: a frozen copy cannot be torn by a late writer.
rm -rf "$SNAP"
mkdir -p "$SNAP/Saves"
cp -a "$DATA_PATH/Saves/." "$SNAP/Saves/"

# serverconfig.json pins "SaveFileLocation" (an absolute path) + the world settings.
members="Saves"
if [[ -f "$DATA_PATH/serverconfig.json" ]]; then
  cp -a "$DATA_PATH/serverconfig.json" "$SNAP/serverconfig.json"
  members="$members serverconfig.json"
fi

# Whitelist/bans/playerdata and mod config live beside the world.
for d in Playerdata ModConfig; do
  if [[ -d "$DATA_PATH/$d" ]]; then
    cp -a "$DATA_PATH/$d" "$SNAP/$d"
    members="$members $d"
  fi
done

tmp="$BACKUP_DEST/saves-$TS.tar.gz.part"
final="$BACKUP_DEST/saves-$TS.tar.gz"

echo "[backup] taring $members -> $final"
# Deliberate word splitting: $members is a tar member list.
# shellcheck disable=SC2086
tar -C "$SNAP" -czf "$tmp" $members
rm -rf "$SNAP"
# Atomic on the NFS share: the tar never appears under its final name until complete.
mv -f "$tmp" "$final"

echo "[backup] pruning to newest 7"
ls -1t "$BACKUP_DEST"/saves-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r -I{} rm -f "{}"
echo "[backup] done"