#!/usr/bin/env bash
# Runs the Vintage Story dedicated server in the foreground (PID 1), so
# Kubernetes owns lifecycle/restart/probes/logs. First run generates
# serverconfig.json under $DATA_PATH; see the plan §8 to pre-configure it.
set -euo pipefail
cd /serverfiles
exec ./VintagestoryServer --dataPath "$DATA_PATH" -p "$PORT"