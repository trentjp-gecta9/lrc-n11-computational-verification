#!/usr/bin/env python3
"""
launch_next_run.py
==================
Watches a running verification run and automatically launches the next one
when the current run reaches its target. Runs entirely locally — no Claude,
no tokens, no open browser tab needed.

Usage
-----
  # Run in background — survives terminal close if using nohup:
  nohup python3 scripts/launch_next_run.py &

  # Or in a spare terminal tab (simpler):
  python3 scripts/launch_next_run.py

What it does
------------
  1. Polls ~/lrc_runs/run_1e8_mech/evidence.db every 60 seconds.
  2. When n_tested >= 100,000,000, launches the 10^9 run automatically.
  3. Logs everything to ~/lrc_runs/launch_next_run.log.
  4. Exits after launching (the monitor script takes over from there).
"""

import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WATCH_DB      = Path("~/lrc_runs/run_1e8_mech/evidence.db").expanduser()
WATCH_TARGET  = 100_000_000

NEXT_SCRIPT   = Path("~/Desktop/Math Ex/reports/lrc_n11_verification/scripts/qwen_lrc_monitor.py").expanduser()
NEXT_RUN_ID   = "run_1e9_mech"
NEXT_TARGET   = 1_000_000_000
NEXT_SEED     = 1337
NEXT_OUT_DIR  = Path("~/lrc_runs").expanduser()

POLL_SECONDS  = 60
LOG_PATH      = Path("~/lrc_runs/launch_next_run.log").expanduser()

# ---------------------------------------------------------------------------

def log(msg: str):
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

def get_n_tested(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
        row  = conn.execute("SELECT SUM(n_tested) FROM batches").fetchone()
        conn.close()
        return int(row[0] or 0)
    except Exception:
        return 0

def main():
    log("Watcher started.")
    log(f"  Watching : {WATCH_DB}")
    log(f"  Target   : {WATCH_TARGET:,}")
    log(f"  Next run : {NEXT_RUN_ID}  (seed={NEXT_SEED}, target={NEXT_TARGET:,})")

    while True:
        n = get_n_tested(WATCH_DB)
        pct = 100 * n / WATCH_TARGET if WATCH_TARGET > 0 else 0
        log(f"Progress: {n:,}/{WATCH_TARGET:,} ({pct:.1f}%)")

        if n >= WATCH_TARGET:
            log("Target reached! Launching 10^9 run...")
            cmd = [
                sys.executable, str(NEXT_SCRIPT),
                "--run-id",     NEXT_RUN_ID,
                "--target",     str(NEXT_TARGET),
                "--seed",       str(NEXT_SEED),
                "--output-dir", str(NEXT_OUT_DIR),
            ]
            log(f"Command: {' '.join(cmd)}")
            subprocess.Popen(cmd, cwd=str(NEXT_SCRIPT.parent))
            log("10^9 run launched. Watcher exiting.")
            break

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
