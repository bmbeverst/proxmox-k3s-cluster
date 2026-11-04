#!/usr/bin/env bash
# Runs the server (PID 1); --stratum-skip-bootstrap keeps starts offline.
set -euo pipefail
cd /serverfiles

# Console FIFO; fd 3 stops EOF latch (kubectl attach).
CONSOLE_FIFO="${CONSOLE_FIFO:-/tmp/vints-console.fifo}"
rm -f "$CONSOLE_FIFO"
mkfifo -m 600 "$CONSOLE_FIFO"
exec 3<>"$CONSOLE_FIFO"

# Each save pauses the tick; interval trades crash risk.
AUTOSAVE_SECONDS=900

# Seed config
if [[ ! -f "$DATA_PATH/serverconfig.json" ]]; then
  echo "[vints] no serverconfig.json found - generating default (--genconfig)"
  ./StratumServer --stratum-skip-bootstrap --genconfig --dataPath "$DATA_PATH"
fi

MAGIC="$DATA_PATH/servermagicnumbers.json"
if [[ ! -f "$MAGIC" ]]; then
  echo "[vints] seeding servermagicnumbers.json with ServerAutoSave=${AUTOSAVE_SECONDS}s"
  printf '{"ServerAutoSave": %s}\n' "$AUTOSAVE_SECONDS" > "$MAGIC"
fi

_current_autosave="$(jq -r '.ServerAutoSave // "unset"' "$MAGIC")"
if [[ "$_current_autosave" != "$AUTOSAVE_SECONDS" ]]; then
  echo "[vints] ServerAutoSave ${_current_autosave} -> ${AUTOSAVE_SECONDS}s"
  jq --argjson v "$AUTOSAVE_SECONDS" '.ServerAutoSave = $v' "$MAGIC" > "$MAGIC.new"
  mv "$MAGIC.new" "$MAGIC"
fi

# Start server
exec ./StratumServer --stratum-skip-bootstrap --dataPath "$DATA_PATH" --port "$PORT" <&3
