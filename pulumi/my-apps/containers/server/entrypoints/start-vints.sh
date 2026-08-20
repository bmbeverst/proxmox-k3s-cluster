#!/usr/bin/env bash
# Runs the Vintage Story dedicated server in the foreground (PID 1), so
# Kubernetes owns lifecycle/restart/probes/logs. First run generates
# serverconfig.json under $DATA_PATH; see the plan §8 to pre-configure it.
set -euo pipefail
cd /serverfiles
# NOTE: the flag is --port, NOT -p. The server's CommandLine parser rejects
# unknown short flags, which makes progArgs null and crashes the server with a
# NullReferenceException in ServerProgram..ctor before it even starts.
exec ./VintagestoryServer --dataPath "$DATA_PATH" --port "$PORT"