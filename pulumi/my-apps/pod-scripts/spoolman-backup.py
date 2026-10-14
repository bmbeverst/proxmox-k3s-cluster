#!/usr/bin/env python3
"""Snapshot Spoolman's SQLite inventory onto the NFS backup share.

Do not tar the data dir: Spoolman runs SQLite in WAL mode, so recent commits
sit in spoolman.db-wal. A plain copy restores stale, silently losing them.
Uses sqlite3's online backup API instead: consistent, no downtime, no RBAC.
Needs the app's uid because reading a WAL database joins its -shm.

    DATA_PATH=/home/app/.local/share/spoolman BACKUP_DEST=/backups \\
        python3 spoolman-backup.py
"""

import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_PATH = os.environ.get("DATA_PATH", "/data")
BACKUP_DEST = os.environ["BACKUP_DEST"]  # NFS mount path
KEEP = max(1, int(os.environ.get("KEEP", "7")))  # never 0: deletes new snapshot
DB_NAME = os.environ.get("DB_NAME", "spoolman.db")
ATTEMPTS = int(os.environ.get("ATTEMPTS", "5"))

src = Path(DATA_PATH) / DB_NAME
dest_dir = Path(BACKUP_DEST)

if not src.exists():
    # First boot: DB not created yet. Not a failure.
    print(f"[backup] no {src} yet; nothing to back up")
    sys.exit(0)

dest_dir.mkdir(parents=True, exist_ok=True)

# Sweep stale .part and sidecars: prune only matches finished snapshots.
for stale in dest_dir.glob("spoolman-*.db.part*"):
    stale.unlink(missing_ok=True)

ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
final = dest_dir / f"spoolman-{ts}.db"
part = final.with_name(final.name + ".part")


def snapshot() -> None:
    """Copy the live DB into `part`, retrying while the source is busy."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            # Read-write on purpose: WAL reads join the -shm index, mode=ro cannot.
            with sqlite3.connect(src) as source, sqlite3.connect(part) as target:
                source.backup(target)
            return
        except sqlite3.OperationalError as exc:
            if attempt == ATTEMPTS:
                raise
            print(f"[backup] source busy ({exc}); retry {attempt}/{ATTEMPTS - 1}")
            time.sleep(5)
            part.unlink(missing_ok=True)


print(f"[backup] snapshotting {src} -> {final}")
try:
    snapshot()
except sqlite3.Error as exc:
    # Corrupt source: fail loudly, leave no partial snapshot to trip the prune.
    part.unlink(missing_ok=True)
    print(f"[backup] could not read {src}: {exc}", file=sys.stderr)
    sys.exit(1)

# Verify, then fold WAL sidecars back so the snapshot is one self-contained file.
# Spoolman re-enables WAL on its next start.
with sqlite3.connect(part) as check:
    verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
    if verdict != "ok":
        check.close()
        part.unlink(missing_ok=True)
        print(f"[backup] integrity_check failed: {verdict}", file=sys.stderr)
        sys.exit(1)
    check.execute("PRAGMA journal_mode=DELETE")

# Conversion removed them, but an earlier half-run may have left one. Only
# matches "<part>-wal"/"<part>-shm", never the snapshot itself.
for leftover in dest_dir.glob(f"{part.name}-*"):
    leftover.unlink(missing_ok=True)

# Atomic on NFS: never visible under the final name until complete and verified.
os.replace(part, final)
print(f"[backup] wrote {final.name} ({final.stat().st_size} bytes)")

# UTC fixed-width timestamps: name order is chronological order.
print(f"[backup] pruning to newest {KEEP}")
finished = sorted(dest_dir.glob("spoolman-*.db"), key=lambda p: p.name, reverse=True)
for old in finished[KEEP:]:
    print(f"[backup] removing {old.name}")
    old.unlink()

print("[backup] done")
