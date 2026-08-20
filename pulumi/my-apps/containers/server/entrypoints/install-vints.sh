#!/usr/bin/env bash
# Installs/updates the Vintage Story server files into /serverfiles (ephemeral)
# and installs/updates mods into $DATA_PATH/Mods (persistent, on the data PVC).
# Idempotent: skips the download when the pinned version is already present.
set -euo pipefail

VS_VERSION="${VS_VERSION:?VS_VERSION is required}"
DATA_PATH="${DATA_PATH:-/data}"
SRV=/serverfiles
MODS_LIST=/config/mods.txt        # mounted from the vints-config ConfigMap
API="http://api.vintagestory.at/stable-unstable.json"

mkdir -p "$SRV"

_current_version() {
  if [[ -x "$SRV/VintagestoryServer" ]]; then
    ( cd "$SRV" && ./VintagestoryServer --version ) 2>/dev/null | tr -d '[:space:]' || true
  fi
}

need_install() {
  local cur; cur="$(_current_version)"
  # Reinstall if missing or if the pinned version differs from what's installed.
  [[ -z "$cur" || "$cur" != *"$VS_VERSION"* ]]
}

if need_install; then
  echo "[install] resolving URL for VS_VERSION=$VS_VERSION"
  url="$(curl -fsSL "$API" | jq -r --arg v "$VS_VERSION" '.[$v].linuxserver.urls.cdn')"
  if [[ "$url" == "null" || -z "$url" ]]; then
    echo "[install] ERROR: no CDN URL for version $VS_VERSION (check version API)" >&2
    exit 1
  fi
  filename="$(basename "$url")"

  echo "[install] downloading $filename"
  curl -fL --retry 3 "$url" -o "/tmp/$filename"

  # Optional but recommended: verify md5 from the same API when available.
  hash="$(curl -fsSL "$API" | jq -r --arg v "$VS_VERSION" '.[$v].linuxserver.md5')"
  if [[ "$hash" != "null" && -n "$hash" ]]; then
    echo "$hash  /tmp/$filename" | md5sum -c - >/dev/null \
      || { echo "[install] md5 mismatch for $filename" >&2; exit 1; }
  fi

  echo "[install] extracting to $SRV"
  rm -rf "$SRV".old
  [[ -d "$SRV" && -n "$(ls -A "$SRV" 2>/dev/null)" ]] && mv "$SRV" "$SRV".old || true
  mkdir -p "$SRV"
  tar -xzf "/tmp/$filename" -C "$SRV"
  chmod +x "$SRV/VintagestoryServer"
  rm -f "/tmp/$filename" && rm -rf "$SRV".old
fi
_installed="$(_current_version)"
echo "[install] server ready: version '${_installed:-UNKNOWN}'"

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
echo "[install] done"