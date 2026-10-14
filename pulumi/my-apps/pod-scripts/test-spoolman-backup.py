#!/usr/bin/env python3
"""Self-test for spoolman-backup.py. Needs only python3 + sqlite3.

Case 1 is the important one: live WAL DB must yield a snapshot with the newest
commits. A naive copy passes "a file appeared" but restores stale, so assert
row counts, not file existence.

    python3 pulumi/my-apps/pod-scripts/test-spoolman-backup.py
"""

import glob
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "spoolman-backup.py"

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def run(data: Path, dest: Path, **env_overrides) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATA_PATH": str(data),
        "BACKUP_DEST": str(dest),
        **{k: str(v) for k, v in env_overrides.items()},
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True
    )


def make_live_db(
    data: Path, committed: int = 10, in_wal: int = 40
) -> sqlite3.Connection:
    """WAL DB with newest rows committed but not checkpointed.

    Returns the open connection on purpose: closing it checkpoints the WAL,
    erasing the condition under test and giving a false pass.
    """
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data / "spoolman.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE spool(id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    for i in range(committed):
        conn.execute("INSERT INTO spool(name) VALUES (?)", (f"old-{i}",))
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    for i in range(in_wal):
        conn.execute("INSERT INTO spool(name) VALUES (?)", (f"new-{i}",))
    conn.commit()
    return conn


def rows_in(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT count(*) FROM spool").fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="spoolman-backup-test-"))
    try:
        # --- 1. WAL commits must not be lost --------------------------------
        print("\n1. live WAL database: snapshot must contain the newest commits")
        data, dest = root / "c1" / "data", root / "c1" / "dest"
        live = make_live_db(data)
        # Guard against a false pass: empty WAL means nothing to lose.
        wal = data / "spoolman.db-wal"
        check(
            "WAL is non-empty (test precondition)",
            wal.exists() and wal.stat().st_size > 0,
            f"{wal.stat().st_size if wal.exists() else 'missing'} bytes",
        )
        proc = run(data, dest)
        check("exit 0", proc.returncode == 0, proc.stderr.strip()[:200])
        snaps = sorted(glob.glob(str(dest / "spoolman-*.db")))
        check("exactly one snapshot", len(snaps) == 1, f"found {len(snaps)}")
        if snaps:
            got = rows_in(Path(snaps[0]))
            # 50 = 10 checkpointed + 40 in WAL. Naive copy yields 10.
            check("all 50 rows present", got == 50, f"got {got}")
            conn = sqlite3.connect(snaps[0])
            check(
                "integrity_check ok",
                conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
            )
            conn.close()

        # --- 2. snapshot is self-contained ----------------------------------
        print("\n2. snapshot is a single self-contained file")
        leftovers = [p for p in os.listdir(dest) if not p.endswith(".db")]
        check("no -wal/-shm sidecars", not leftovers, f"{leftovers}")
        check("no .part debris", not glob.glob(str(dest / "*.part*")))

        # --- 3. rotation ----------------------------------------------------
        print("\n3. rotation keeps the newest KEEP snapshots")
        dest3 = root / "c3" / "dest"
        dest3.mkdir(parents=True)
        for ts in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
            (dest3 / f"spoolman-{ts}.db").write_text("old")
        proc = run(data, dest3, KEEP=3)
        kept = sorted(p.name for p in dest3.glob("spoolman-*.db"))
        check("exit 0", proc.returncode == 0, proc.stderr.strip()[:200])
        check("3 kept", len(kept) == 3, f"{kept}")
        check("oldest pruned", "spoolman-20200101T000000Z.db" not in kept, f"{kept}")

        # --- 4. stale .part sweep -------------------------------------------
        print("\n4. stale .part files (including sidecars) are swept")
        dest4 = root / "c4" / "dest"
        dest4.mkdir(parents=True)
        (dest4 / "spoolman-20200101T000000Z.db.part").write_text("stale")
        (dest4 / "spoolman-20200102T000000Z.db.part-wal").write_text("stale")
        (dest4 / "spoolman-20200103T000000Z.db.part-shm").write_text("stale")
        proc = run(data, dest4)
        check("exit 0", proc.returncode == 0, proc.stderr.strip()[:200])
        check("no .part left", not glob.glob(str(dest4 / "*.part*")))

        # --- 5. no database yet ---------------------------------------------
        print("\n5. no database yet (first boot) exits 0")
        empty, dest5 = root / "c5" / "data", root / "c5" / "dest"
        empty.mkdir(parents=True)
        proc = run(empty, dest5)
        check("exit 0", proc.returncode == 0, proc.stderr.strip()[:200])
        check("nothing written", not list(dest5.glob("*")))

        # --- 6. corrupt source ----------------------------------------------
        print("\n6. corrupt source fails loudly and leaves no debris")
        bad, dest6 = root / "c6" / "data", root / "c6" / "dest"
        bad.mkdir(parents=True)
        (bad / "spoolman.db").write_bytes(os.urandom(5000))
        proc = run(bad, dest6)
        check("non-zero exit", proc.returncode != 0)
        check("no debris", not list(dest6.glob("*")), f"{list(dest6.glob('*'))}")

        # --- 7. KEEP=0 must not delete the new snapshot ---------------------
        print("\n7. KEEP=0 is clamped to 1 (never deletes the fresh snapshot)")
        dest7 = root / "c7" / "dest"
        proc = run(data, dest7, KEEP=0)
        kept7 = list(dest7.glob("spoolman-*.db"))
        check("exit 0", proc.returncode == 0, proc.stderr.strip()[:200])
        check("snapshot survives", len(kept7) == 1, f"{len(kept7)} kept")
    finally:
        live.close()
        shutil.rmtree(root, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:")
        for name, _, detail in failed:
            print(f"  - {name}" + (f"  [{detail}]" if detail else ""))
        return 1
    print("spoolman-backup self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
