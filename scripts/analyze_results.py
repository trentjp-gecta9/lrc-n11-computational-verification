#!/usr/bin/env python3
"""
analyze_results.py
==================
Query and summarise the LRC n=11 verification evidence stored in
evidence.db and batches.jsonl.

Usage
-----
  python3 analyze_results.py --run-dir runs/run_YYYYMMDD_HHMMSS

  Options:
    --run-dir PATH    Path to run directory (required)
    --top-margins N   Print N configurations with smallest witness margin
    --histogram       Print ASCII histogram of min_margin distribution
    --sign-stats      Break down results by sign class (requires sign data)

Author: Trent Palelei
Date  : 2026-03-30
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def load_summary(run_dir: Path) -> dict:
    p = run_dir / "summary.json"
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def cumulative_from_db(conn) -> dict:
    row = conn.execute(
        """SELECT COUNT(*), SUM(n_tested), SUM(n_passed), SUM(n_failed),
                  SUM(total_boundaries), MIN(min_margin), MAX(max_margin),
                  SUM(elapsed_s), MIN(batch_index), MAX(batch_index)
           FROM batches"""
    ).fetchone()
    return {
        "n_batches":          row[0] or 0,
        "n_tested":           row[1] or 0,
        "n_passed":           row[2] or 0,
        "n_failed":           row[3] or 0,
        "total_boundaries":   row[4] or 0,
        "global_min_margin":  row[5],
        "global_max_margin":  row[6],
        "total_elapsed_s":    row[7] or 0,
        "first_batch":        row[8],
        "last_batch":         row[9],
    }


def print_summary(stats: dict, summary: dict):
    print("\n" + "=" * 64)
    print("  LRC n=11 Verification — Evidence Summary")
    print("=" * 64)
    print(f"  Configurations tested : {stats['n_tested']:,}")
    print(f"  Witnesses found       : {stats['n_passed']:,}")
    print(f"  Failures              : {stats['n_failed']}")
    if stats["n_tested"] > 0:
        rate = stats["n_passed"] / stats["n_tested"]
        print(f"  Success rate          : {100*rate:.8f}%")
    print(f"  Batches completed     : {stats['n_batches']:,}")
    print(f"  (batch index range    : [{stats['first_batch']}, {stats['last_batch']}])")
    print()
    print(f"  Total boundaries eval : {stats['total_boundaries']:,}")
    if stats["n_tested"] > 0:
        print(f"  Mean boundaries/config: "
              f"{stats['total_boundaries']/stats['n_tested']:.1f}")
    print()
    print(f"  Global min margin     : {stats['global_min_margin']:.8f}")
    print(f"  Global max margin     : {stats['global_max_margin']:.8f}")
    print()
    t = stats["total_elapsed_s"] or 0
    print(f"  Total elapsed         : {t:.1f}s  ({t/3600:.2f}h)")
    if stats["n_tested"] > 0 and t > 0:
        print(f"  Throughput            : {stats['n_tested']/t:.0f} configs/s")
    print()

    if stats["n_failed"] == 0 and stats["n_tested"] > 0:
        p_up = 3.0 / stats["n_tested"]
        print(f"  95% conf. bound on failure rate : < {p_up:.2e}")
        print(f"  (fewer than 1 failure per {1/p_up:.0f} configurations)")
        print()
        print("  OUTCOME: No counterexample detected.")
    else:
        print(f"  OUTCOME: {stats['n_failed']} failure(s) — inspect failures table.")

    print("=" * 64)


def print_failures(conn, limit: int = 20):
    rows = conn.execute(
        """SELECT f.batch_index, f.speeds_json, f.margin, f.n_bound
           FROM failures f ORDER BY f.margin ASC LIMIT ?""",
        (limit,)
    ).fetchall()
    if not rows:
        print("\n  No failures recorded.")
        return
    print(f"\n  Top {len(rows)} failure(s) by margin:")
    print(f"  {'batch':>8}  {'margin':>12}  {'n_bound':>8}  speeds")
    print(f"  {'-'*8}  {'-'*12}  {'-'*8}  -------")
    for batch_idx, speeds_json, margin, n_bound in rows:
        speeds = json.loads(speeds_json)
        sp_str = "[" + ", ".join(f"{x:.4f}" for x in speeds) + "]"
        print(f"  {batch_idx:>8}  {margin:>12.8f}  {n_bound:>8}  {sp_str}")


def ascii_histogram(values: list, n_bins: int = 20, width: int = 50) -> str:
    if not values:
        return "  (no data)"
    lo, hi = min(values), max(values)
    if lo == hi:
        return f"  All values = {lo:.6f}"
    bins = [0] * n_bins
    for v in values:
        idx = int((v - lo) / (hi - lo) * n_bins)
        if idx >= n_bins:
            idx = n_bins - 1
        bins[idx] += 1
    max_count = max(bins) or 1
    lines = [f"  min_margin distribution  (n={len(values):,})"]
    for i, count in enumerate(bins):
        lo_b = lo + i * (hi - lo) / n_bins
        bar  = "█" * int(count / max_count * width)
        lines.append(f"  {lo_b:8.5f} | {bar:<{width}} {count:,}")
    return "\n".join(lines)


def print_histogram(conn):
    rows = conn.execute("SELECT min_margin FROM batches").fetchall()
    values = [r[0] for r in rows if r[0] is not None]
    print("\n" + ascii_histogram(values))


def main():
    parser = argparse.ArgumentParser(
        description="Analyse LRC n=11 verification evidence."
    )
    parser.add_argument("--run-dir", required=True,
                        help="Path to run directory containing evidence.db.")
    parser.add_argument("--top-margins", type=int, default=10,
                        help="Print N configs with smallest witness margin.")
    parser.add_argument("--histogram", action="store_true",
                        help="Print ASCII histogram of min_margin distribution.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    db_path = run_dir / "evidence.db"

    if not db_path.exists():
        print(f"Error: evidence.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn    = sqlite3.connect(str(db_path))
    stats   = cumulative_from_db(conn)
    summary = load_summary(run_dir)

    print_summary(stats, summary)
    print_failures(conn, limit=args.top_margins)

    if args.histogram:
        print_histogram(conn)

    conn.close()


if __name__ == "__main__":
    main()
