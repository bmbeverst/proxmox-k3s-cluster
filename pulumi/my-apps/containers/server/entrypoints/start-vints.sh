#!/usr/bin/env bash
# Runs the Vintage Story dedicated server in the foreground (PID 1), so
# Kubernetes owns lifecycle/restart/probes/logs. First run generates
# serverconfig.json under $DATA_PATH.
set -euo pipefail
cd /serverfiles

# Seed config
if [[ ! -f "$DATA_PATH/serverconfig.json" ]]; then
  echo "[vints] no serverconfig.json found - generating default (--genconfig)"
  ./VintagestoryServer --genconfig --dataPath "$DATA_PATH"
fi

# Start server
exec ./VintagestoryServer --dataPath "$DATA_PATH" --port "$PORT"
