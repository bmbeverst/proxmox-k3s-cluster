#!/usr/bin/env bash
# Runs the Vintage Story dedicated server in the foreground (PID 1), so
# Kubernetes owns lifecycle/restart/probes/logs. First run generates
# serverconfig.json under $DATA_PATH.
set -euo pipefail
cd /serverfiles

# Console commands arrive on this FIFO instead of the pod's tty: a tty client that
# disconnects (kubectl attach) sends EOF, which .NET latches, after which the server
# ignores console input for the rest of the pod's life. fd 3 keeps a write end open
# here, so the FIFO itself never reaches EOF. Talk to it with vints-console.
# The pod env sets the path; the default keeps the CI smoke test and local runs working.
CONSOLE_FIFO="${CONSOLE_FIFO:-/tmp/vints-console.fifo}"
rm -f "$CONSOLE_FIFO"
mkfifo -m 600 "$CONSOLE_FIFO"
exec 3<>"$CONSOLE_FIFO"

# Each world save blocks the main tick thread while the chunk DB is flushed, so
# the interval in seconds trades unsaved progress on a crash for fewer pauses.
AUTOSAVE_SECONDS=900

# Seed config
if [[ ! -f "$DATA_PATH/serverconfig.json" ]]; then
  echo "[vints] no serverconfig.json found - generating default (--genconfig)"
  ./VintagestoryServer --genconfig --dataPath "$DATA_PATH"
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
exec ./VintagestoryServer --dataPath "$DATA_PATH" --port "$PORT" <&3
