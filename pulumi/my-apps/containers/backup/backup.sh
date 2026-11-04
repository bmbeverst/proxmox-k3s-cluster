#!/usr/bin/env bash
# Tar world + config to NFS; keep 7 newest.
set -euo pipefail

DATA_PATH="${DATA_PATH:-/data}"
BACKUP_DEST="${BACKUP_DEST:?BACKUP_DEST required}"   # mount path of the NFS share
SNAP="$DATA_PATH/.backup-snap"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

# Remove stale .part files the prune would miss.
rm -f "$BACKUP_DEST"/saves-*.tar.gz.part

if [[ ! -d "$DATA_PATH/Saves" ]]; then
  echo "[backup] no Saves dir yet; nothing to back up"
  exit 0
fi

# Snapshot the world first: avoids a torn copy.
rm -rf "$SNAP"
mkdir -p "$SNAP/Saves"
cp -a "$DATA_PATH/Saves/." "$SNAP/Saves/"

# serverconfig.json pins SaveFileLocation + world settings.
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
# Atomic rename: the tar appears only when complete.
mv -f "$tmp" "$final"

echo "[backup] pruning to newest 7"
ls -1t "$BACKUP_DEST"/saves-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r -I{} rm -f "{}"
echo "[backup] done"