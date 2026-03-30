#!/usr/bin/env python3
"""
qwen_lrc_monitor.py
===================
Autonomous monitor for the LRC n=11 verification campaign.

Launches verify_lrc_n11_v2.py as a subprocess, auto-restarts it with
--resume if it dies, polls the SQLite evidence.db every few minutes,
and calls local Qwen (via Ollama) for periodic status summaries.

Usage
-----
  # Start a new 10^8 run (leave terminal open or use nohup/screen):
  python3 qwen_lrc_monitor.py \\
      --run-id run_1e8_mech \\
      --target 100000000 \\
      --output-dir ~/lrc_runs

  # Resume after interruption (monitor detects existing DB and passes --resume):
  python3 qwen_lrc_monitor.py \\
      --run-id run_1e8_mech \\
      --target 100000000 \\
      --output-dir ~/lrc_runs \\
      --resume

  # Smoke test (2000 configs, 10-second poll interval):
  python3 qwen_lrc_monitor.py \\
      --run-id smoke_v2 --target 2000 \\
      --output-dir ~/lrc_runs --poll-interval 10

Flags
-----
  --run-id STR          Run identifier passed to verifier
  --target INT          Total configs (passed to verifier)
  --batch-size INT      Batch size (default 5000)
  --workers INT         Parallel workers (default cpu_count)
  --seed INT            Master random seed (default 42)
  --output-dir PATH     Evidence root (default ~/lrc_runs)
  --resume              Pass --resume to verifier on first launch
  --poll-interval INT   Seconds between DB polls (default 300)
  --qwen-interval INT   Seconds between Qwen summary calls (default 3600)
  --no-qwen             Skip Qwen calls (use if Ollama not running)
  --script PATH         Path to verify_lrc_n11_v2.py (auto-detected)

Output
------
  <output_dir>/<run_id>/monitor.log    — heartbeat + Qwen summaries
  (verifier writes batches.jsonl, evidence.db, summary.json as usual)

Author: Trent Palelei
Date  : 2026-03-30
"""

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Qwen integration (mirrors tools/qwen_subagent.py pattern)
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://localhost:11434/api/chat"
QWEN_MODEL = "qwen3.5:4b"
QWEN_TIMEOUT = 120   # seconds


def _qwen_summarise(stats: dict, region_stats: list, log_path: Path) -> Optional[str]:
    """
    Call local Qwen via Ollama to produce a 2-3 sentence progress summary
    suitable for a paper appendix.  Returns the response text or None on failure.
    """
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        _log(log_path, "Qwen: requests not installed — skipping summary.")
        return None

    n      = stats.get("n_tested", 0)
    failed = stats.get("n_failed", 0)
    mm     = stats.get("global_min_margin")
    mm_str = f"{mm:.6f}" if mm is not None else "N/A"

    # Build region breakdown string
    region_lines = []
    for rs in region_stats:
        region_lines.append(
            f"  {rs['region']}: n={rs['n_configs']:,}, "
            f"min_margin={rs['min_margin']:.6f}"
        )
    region_str = "\n".join(region_lines) if region_lines else "  (no data yet)"

    prompt = (
        f"LRC n=11 verification progress report:\n"
        f"  Configurations tested: {n:,}\n"
        f"  Failures (no witness): {failed}\n"
        f"  Global minimum isolation margin: {mm_str}\n"
        f"  Breakdown by analytic region:\n{region_str}\n\n"
        f"In 2-3 sentences suitable for a mathematical paper appendix, "
        f"summarise what these results show about the verification campaign."
    )

    payload = {
        "model":    QWEN_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream":   False,
        "options":  {"num_predict": 300, "temperature": 0.3},
    }

    try:
        import requests
        resp = requests.post(OLLAMA_URL, json=payload, timeout=QWEN_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "").strip()
    except Exception as exc:
        _log(log_path, f"Qwen call failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------

def _query_stats(db_path: Path) -> tuple:
    """
    Returns (cumulative_stats_dict, region_stats_list) from evidence.db.
    Returns (None, []) if DB doesn't exist yet.
    """
    if not db_path.exists():
        return None, []
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            """SELECT COUNT(*), SUM(n_tested), SUM(n_passed), SUM(n_failed),
                      SUM(total_boundaries), MIN(min_margin), MAX(max_margin),
                      SUM(sigma2_neg), SUM(sigma2_pos)
               FROM batches"""
        ).fetchone()
        stats = {
            "n_batches":         row[0] or 0,
            "n_tested":          row[1] or 0,
            "n_passed":          row[2] or 0,
            "n_failed":          row[3] or 0,
            "total_boundaries":  row[4] or 0,
            "global_min_margin": row[5],
            "global_max_margin": row[6],
            "sigma2_neg":        row[7] or 0,
            "sigma2_pos":        row[8] or 0,
        }
        reg_rows = conn.execute(
            """SELECT region, SUM(n_configs), MIN(min_margin), MAX(max_margin)
               FROM branch_stats GROUP BY region ORDER BY region"""
        ).fetchall()
        region_stats = [
            {"region": r[0], "n_configs": r[1], "min_margin": r[2], "max_margin": r[3]}
            for r in reg_rows
        ]
        conn.close()
        return stats, region_stats
    except Exception as exc:
        return None, []


def _is_complete(db_path: Path, target: int) -> bool:
    """Return True if n_tested >= target (run finished)."""
    stats, _ = _query_stats(db_path)
    if stats is None:
        return False
    return stats.get("n_tested", 0) >= target


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(log_path: Path, msg: str):
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(log_path, "a") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Verifier subprocess management
# ---------------------------------------------------------------------------

def _build_verifier_cmd(
    script_path: Path,
    run_id: str,
    target: int,
    batch_size: int,
    workers: int,
    seed: int,
    output_dir: Path,
    resume: bool,
) -> list:
    cmd = [
        sys.executable, str(script_path),
        "--run-id",     run_id,
        "--target",     str(target),
        "--batch-size", str(batch_size),
        "--workers",    str(workers),
        "--seed",       str(seed),
        "--output-dir", str(output_dir),
    ]
    if resume:
        cmd.append("--resume")
    return cmd


def _launch_verifier(cmd: list, log_path: Path) -> subprocess.Popen:
    _log(log_path, f"Launching verifier: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return proc


# ---------------------------------------------------------------------------
# Main monitor loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous monitor for LRC n=11 verification (qwen_lrc_monitor)."
    )
    default_out = str(Path("~/lrc_runs").expanduser())
    parser.add_argument("--run-id",        type=str,  required=True)
    parser.add_argument("--target",        type=int,  default=100_000_000)
    parser.add_argument("--batch-size",    type=int,  default=5_000)
    parser.add_argument("--workers",       type=int,  default=None)
    parser.add_argument("--seed",          type=int,  default=42)
    parser.add_argument("--output-dir",    type=str,  default=default_out)
    parser.add_argument("--resume",        action="store_true")
    parser.add_argument("--poll-interval", type=int,  default=300,
                        help="Seconds between DB polls (default 300).")
    parser.add_argument("--qwen-interval", type=int,  default=3600,
                        help="Seconds between Qwen summary calls (default 3600).")
    parser.add_argument("--no-qwen",       action="store_true",
                        help="Disable Qwen calls (use if Ollama not running).")
    parser.add_argument("--script",        type=str,  default=None,
                        help="Path to verify_lrc_n11_v2.py (auto-detected if omitted).")
    args = parser.parse_args()

    # Auto-detect script path
    if args.script:
        script_path = Path(args.script).resolve()
    else:
        # Same directory as this monitor script
        script_path = (Path(__file__).parent / "verify_lrc_n11_v2.py").resolve()

    if not script_path.exists():
        print(f"ERROR: verifier script not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir).expanduser().resolve()
    run_dir    = output_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path    = run_dir / "evidence.db"
    log_path   = run_dir / "monitor.log"

    from multiprocessing import cpu_count
    n_workers = args.workers or cpu_count()

    _log(log_path, "=" * 60)
    _log(log_path, f"qwen_lrc_monitor starting")
    _log(log_path, f"  run_id     : {args.run_id}")
    _log(log_path, f"  target     : {args.target:,}")
    _log(log_path, f"  output_dir : {output_dir}")
    _log(log_path, f"  script     : {script_path}")
    _log(log_path, f"  poll every : {args.poll_interval}s")
    _log(log_path, f"  qwen every : {args.qwen_interval}s")
    _log(log_path, f"  qwen       : {'disabled' if args.no_qwen else 'enabled'}")
    _log(log_path, "=" * 60)

    # Graceful shutdown flag
    stop = [False]
    def _handler(sig, frame):
        stop[0] = True
        _log(log_path, "Monitor interrupted — will stop after current poll.")
    signal.signal(signal.SIGINT,  _handler)
    signal.signal(signal.SIGTERM, _handler)

    proc:          Optional[subprocess.Popen] = None
    last_poll      = 0.0
    last_qwen      = 0.0
    first_launch   = True
    resume_flag    = args.resume

    while not stop[0]:
        now = time.monotonic()

        # --- Check if already done ---
        if _is_complete(db_path, args.target):
            _log(log_path, f"Target {args.target:,} reached. Verification complete.")
            if proc and proc.poll() is None:
                proc.terminate()
            break

        # --- Drain subprocess stdout ---
        if proc and proc.poll() is None:
            # Non-blocking read of any pending lines
            try:
                import select
                readable, _, _ = select.select([proc.stdout], [], [], 0)
                if readable:
                    for _ in range(50):   # read up to 50 lines at once
                        line = proc.stdout.readline()
                        if not line:
                            break
                        print(line.rstrip(), flush=True)
            except Exception:
                pass

        # --- (Re-)launch verifier if not running ---
        if proc is None or proc.poll() is not None:
            exit_code = proc.poll() if proc else None
            if proc is not None:
                # drain remaining output
                remaining = proc.stdout.read()
                if remaining:
                    for ln in remaining.splitlines():
                        print(ln, flush=True)
                if exit_code == 0:
                    _log(log_path, f"Verifier exited cleanly (code 0).")
                    if _is_complete(db_path, args.target):
                        break
                    _log(log_path, "Target not reached — relaunching with --resume.")
                else:
                    _log(log_path, f"Verifier exited with code {exit_code} — relaunching.")

            cmd = _build_verifier_cmd(
                script_path   = script_path,
                run_id        = args.run_id,
                target        = args.target,
                batch_size    = args.batch_size,
                workers       = n_workers,
                seed          = args.seed,
                output_dir    = output_dir,
                resume        = (resume_flag or not first_launch),
            )
            proc        = _launch_verifier(cmd, log_path)
            first_launch = False
            resume_flag  = True   # always resume after first launch

        # --- Periodic DB poll ---
        if now - last_poll >= args.poll_interval:
            last_poll = now
            stats, region_stats = _query_stats(db_path)
            if stats:
                n      = stats["n_tested"]
                failed = stats["n_failed"]
                pct    = 100 * n / args.target if args.target > 0 else 0
                mm     = stats["global_min_margin"]
                mm_str = f"{mm:.6f}" if mm is not None else "N/A"
                _log(log_path,
                     f"Progress: {n:,}/{args.target:,} ({pct:.1f}%)  "
                     f"failed={failed}  min_margin={mm_str}  "
                     f"batches={stats['n_batches']}")
                if region_stats:
                    for rs in region_stats:
                        _log(log_path,
                             f"  {rs['region']:12s}: n={rs['n_configs']:>10,}  "
                             f"min={rs['min_margin']:.6f}")
                if failed > 0:
                    _log(log_path, f"!!! {failed} FAILURE(S) DETECTED — inspect evidence.db !!!")
            else:
                _log(log_path, "DB not available yet — verifier may still be initialising.")

        # --- Periodic Qwen summary ---
        if (not args.no_qwen) and (now - last_qwen >= args.qwen_interval):
            last_qwen = now
            stats, region_stats = _query_stats(db_path)
            if stats and stats["n_tested"] > 0:
                _log(log_path, "Calling Qwen for status summary...")
                summary = _qwen_summarise(stats, region_stats, log_path)
                if summary:
                    _log(log_path, f"Qwen summary:\n{summary}")
                else:
                    _log(log_path, "Qwen returned no summary.")

        # Sleep briefly to avoid busy-looping
        time.sleep(min(5, args.poll_interval))

    # Final status
    stats, region_stats = _query_stats(db_path)
    if stats:
        n      = stats["n_tested"]
        failed = stats["n_failed"]
        _log(log_path, "=" * 60)
        _log(log_path, f"FINAL: {n:,} tested, {failed} failed, "
                       f"min_margin={stats['global_min_margin']}")
        if failed == 0 and n > 0:
            p_up = 3.0 / n
            _log(log_path, f"95%% CI failure rate < {p_up:.2e}")
        if not args.no_qwen and stats["n_tested"] > 0:
            _log(log_path, "Requesting final Qwen report...")
            final_summary = _qwen_summarise(stats, region_stats, log_path)
            if final_summary:
                _log(log_path, f"Final Qwen report:\n{final_summary}")
                # Write to separate file for easy copy-paste into paper
                report_path = run_dir / "final_report_qwen.txt"
                with open(report_path, "w") as f:
                    f.write(final_summary + "\n")
                _log(log_path, f"Final report written to {report_path}")
        _log(log_path, "Monitor finished.")
    else:
        _log(log_path, "Monitor finished — no data collected.")


if __name__ == "__main__":
    main()
