#!/usr/bin/env bash
# Runs the Vintage Story dedicated server in the foreground (PID 1), so
# Kubernetes owns lifecycle/restart/probes/logs. First run generates
# serverconfig.json under $DATA_PATH.
set -euo pipefail
cd /serverfiles

# Seed-once: on a brand-new data dir (no serverconfig.json yet) ask the server to
# generate a default config and exit. Any existing config (created world /
# in-game /serverconfig edits) is left untouched, so local settings survive
# restarts.
if [[ ! -f "$DATA_PATH/serverconfig.json" ]]; then
  echo "[vints] no serverconfig.json found - generating default (--genconfig)"
  ./VintagestoryServer --genconfig --dataPath "$DATA_PATH"
fi

# NOTE: the flag is --port, NOT -p. The server's CommandLine parser rejects
# unknown short flags, which makes progArgs null and crashes the server with a
# NullReferenceException in ServerProgram..ctor before it even starts.
exec ./VintagestoryServer --dataPath "$DATA_PATH" --port "$PORT"
