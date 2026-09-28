#!/usr/bin/env bash
# Syncs the server's mods into $DATA_PATH/Mods (persistent, on the data PVC) from the
# vints-config ConfigMap. Idempotent: a mod is re-fetched only when its URL changes.
set -euo pipefail

DATA_PATH="${DATA_PATH:-/data}"
MODS_LIST=/config/mods.txt        # mounted from the vints-config ConfigMap

# --- Mods ----------------------------------------------------------------
# mods.txt lines are "<mod-id> <direct .zip URL>". Installed as
# ${DATA_PATH}/Mods/<id>.zip; a sidecar <id>.url stamps the exact URL so that:
#   - keyed on <id> (not the URL basename) => .../latest URLs never collide;
#   - updating the URL re-downloads (idempotent), keeping existing files untouched.
# Prefix a line with '#' to ignore it. To fully remove a mod, delete its .zip/.url.
mkdir -p "$DATA_PATH/Mods"
if [[ -f "$MODS_LIST" ]]; then
  while read -r id url; do
    id="${id%%[[:space:]]*}"
    [[ -z "$id" || "$id" == \#* ]] && continue
    [[ -n "$url" ]] || { echo "[mods] skip: no url for '$id'"; continue; }
    dest="$DATA_PATH/Mods/${id}.zip"
    stamp="$DATA_PATH/Mods/${id}.url"
    if [[ -f "$dest" && -f "$stamp" && "$(cat "$stamp")" == "$url" ]]; then
      echo "[mods] up-to-date: $id"
      continue
    fi
    echo "[mods] installing: $id"
    curl -fL --retry 3 "$url" -o "$dest.tmp" \
      && mv "$dest.tmp" "$dest" \
      && printf '%s' "$url" > "$stamp"
  done < "$MODS_LIST"
fi
echo "[mods] done"
