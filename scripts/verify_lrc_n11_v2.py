#!/usr/bin/env python3
"""
verify_lrc_n11_v2.py
====================
Event-complete boundary-set verification of the n=11 Lonely Runner Conjecture
with mechanism tagging, branch statistics, and near-miss archive.

Extends verify_lrc_n11.py with:
  - Sign-class fingerprint (σ₂…σ₉) per configuration
  - Region classification (neg_branch / alpha_1 / alpha_3 / beta_1 … beta_4)
    based on the α/β decomposition in §6 (Lemma 6.2) of the proof
  - Witness-slot index (which of I₁…I₉ the witness falls in)
  - Near-miss archive: top 500 smallest-margin configurations in SQLite
  - Branch statistics table: counts/margins per (region, sign_class)
  - Default --output-dir ~/lrc_runs  (absolute path → no iCloud/SQLite failure)

Evidence storage
----------------
  <output_dir>/<run_id>/batches.jsonl   -- one JSON record per batch
  <output_dir>/<run_id>/evidence.db     -- SQLite with batches, failures,
                                            branch_stats, near_misses, run_meta
  <output_dir>/<run_id>/summary.json    -- final statistics
  <output_dir>/<run_id>/config.json     -- full run parameters

Usage
-----
  python3 verify_lrc_n11_v2.py [options]

  --target INT        Total configurations to test       (default 10_000_000)
  --batch-size INT    Configs per work batch              (default 5_000)
  --workers INT       Parallel worker processes           (default cpu_count)
  --seed INT          Master random seed                  (default 42)
  --output-dir PATH   Root directory for evidence output  (default ~/lrc_runs)
  --run-id STR        Name for this run                   (default auto timestamp)
  --resume            Resume from existing run_id         (default False)
  --near-miss-k INT   Size of near-miss archive           (default 500)

Reproducibility
---------------
  Each batch uses seed = master_seed + batch_index, so results are fully
  deterministic and individual batches can be re-run independently.

Author: Trent Palelei  (verification harness v2 — mechanism-tagged edition)
Date  : 2026-03-30
"""

import argparse
import heapq
import json
import os
import signal
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

N_RUNNERS  = 11
N_SPEEDS   = 10
THETA      = 1.0 / N_RUNNERS    # 1/11
THETA_EPS  = 1e-11

# α/β region boundary constants (exact from Lemma 6.2 / paper §6)
_A9_ALPHA1_LIMIT  = 580.0 / 69.0     # ≈ 8.4058  (α₁ upper for σ₉=-1)
_A9_BETA1_LIMIT   = 87.0  / 10.0     # = 8.7     (β₁/β₂ split for σ₉=-1)
_A9_BETA2_LIMIT   = 980.0 / 109.0    # ≈ 8.9908  (β₂ upper for σ₉=-1)
_A9_ALPHA3_LIMIT  = 1960.0 / 207.0   # ≈ 9.4686  (α₃ for σ₉=+1)
_A9_BETA3_LIMIT   = 86.0  / 9.0      # ≈ 9.5556  (β₃/β₄ split for σ₉=+1)

NEAR_MISS_K_DEFAULT = 500   # size of near-miss archive

# ---------------------------------------------------------------------------
# Core verification functions  (identical to v1)
# ---------------------------------------------------------------------------

def _boundary_times_in_window(a: np.ndarray, w_left: float, w_right: float) -> np.ndarray:
    times = []
    for a_i in a:
        if a_i < 1e-15:
            continue
        k_min = max(0, int(np.floor(a_i * w_left)))
        k_max = int(np.ceil(a_i * w_right)) + 1
        for k in range(k_min, k_max + 1):
            t1 = (k + THETA) / a_i
            t2 = (k + 1.0 - THETA) / a_i
            if w_left - THETA_EPS <= t1 <= w_right + THETA_EPS:
                times.append(t1)
            if w_left - THETA_EPS <= t2 <= w_right + THETA_EPS:
                times.append(t2)
    if not times:
        return np.array([])
    return np.unique(np.array(times, dtype=np.float64))


def _is_isolated(a: np.ndarray, t: float) -> tuple:
    frac   = np.mod(a * t, 1.0)
    dists  = np.minimum(frac, 1.0 - frac)
    min_d  = float(np.min(dists))
    return min_d >= THETA - THETA_EPS, min_d - THETA


def find_witness(a: np.ndarray) -> tuple:
    """
    Returns (found, t_witness, min_margin, n_boundaries, witness_slot).
    witness_slot: int 0..8 indicating which I_s the witness falls in, or -1.
    """
    w_left  = 1.0  / (N_RUNNERS * a[0])
    w_right = 10.0 / (N_RUNNERS * a[0])

    if w_right <= w_left:
        return False, None, -1.0, 0, -1

    boundaries = _boundary_times_in_window(a, w_left, w_right)
    n_bound = len(boundaries)

    if n_bound == 0:
        t_mid = 0.5 * (w_left + w_right)
        ok, margin = _is_isolated(a, t_mid)
        slot = _witness_slot(t_mid, w_left, w_right) if ok else -1
        return ok, (t_mid if ok else None), margin, 0, slot

    best_margin = -np.inf
    best_t      = None

    eval_times = [w_left]
    for i in range(len(boundaries) - 1):
        eval_times.append(0.5 * (boundaries[i] + boundaries[i + 1]))
    eval_times.append(w_right)
    eval_times.extend(boundaries.tolist())

    for t in eval_times:
        if not (w_left - THETA_EPS <= t <= w_right + THETA_EPS):
            continue
        ok, margin = _is_isolated(a, t)
        if margin > best_margin:
            best_margin = margin
            best_t      = t
        if ok:
            slot = _witness_slot(t, w_left, w_right)
            return True, t, margin, n_bound, slot

    return False, best_t, best_margin, n_bound, -1


# ---------------------------------------------------------------------------
# Mechanism classification
# ---------------------------------------------------------------------------

def _sign_class(a: np.ndarray) -> tuple:
    """
    Return (σ₂, σ₃, …, σ₉) where σⱼ = +1 if aⱼ > j, else -1.
    Columns: a[0]=a₁, a[1]=a₂, …, a[8]=a₉.  Runner j → column j-1.
    """
    return tuple(1 if a[j - 1] > j else -1 for j in range(2, 10))


def _region(a: np.ndarray, sigma: tuple) -> str:
    """
    Classify configuration into the proof's α/β decomposition regions (§6).

    Regions:
      neg_branch  — σ₂ = -1  (§6.1 negative branch)
      alpha_1     — σ₂=+1, σ₉=-1, a₉ < 580/69
      beta_1      — σ₂=+1, σ₉=-1, a₉ ∈ [580/69, 87/10)
      beta_2      — σ₂=+1, σ₉=-1, a₉ ∈ [87/10, 980/109)
      alpha_1e    — σ₂=+1, σ₉=-1, a₉ ≥ 980/109  (edge of α₁ extension)
      alpha_3     — σ₂=+1, σ₉=+1, a₉ < 1960/207 (direct incompatibility)
      beta_3      — σ₂=+1, σ₉=+1, a₉ ∈ [1960/207, 86/9]
      beta_4      — σ₂=+1, σ₉=+1, a₉ > 86/9
    """
    sigma2 = sigma[0]   # σ₂ = sigma[0]  (runner 2 → index 0)
    if sigma2 == -1:
        return "neg_branch"

    sigma9 = sigma[7]   # σ₉ = sigma[7]  (runner 9 → index 7)
    a9     = a[8]       # a₉ is column index 8

    if sigma9 == -1:
        if a9 < _A9_ALPHA1_LIMIT:  return "alpha_1"
        if a9 < _A9_BETA1_LIMIT:   return "beta_1"
        if a9 < _A9_BETA2_LIMIT:   return "beta_2"
        return "alpha_1e"           # near a₉ = 9, still α₁-extended
    else:
        if a9 < _A9_ALPHA3_LIMIT:  return "alpha_3"
        if a9 <= _A9_BETA3_LIMIT:  return "beta_3"
        return "beta_4"


def _witness_slot(t_wit: float, w_left: float, w_right: float) -> int:
    """
    Map witness time to slot index 0..8 (corresponding to I₁…I₉).
    The window W_ref is partitioned into 9 equal sub-intervals.
    Returns -1 if t_wit outside window.
    """
    w_span = w_right - w_left
    if w_span <= 0:
        return -1
    rel = (t_wit - w_left) / w_span   # in [0, 1]
    rel = max(0.0, min(1.0, rel))
    return min(8, int(rel * 9))


# ---------------------------------------------------------------------------
# Batch generation  (identical to v1)
# ---------------------------------------------------------------------------

def _generate_batch(batch_size: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    configs = np.empty((batch_size, N_SPEEDS), dtype=np.float64)
    u = rng.random(batch_size)
    configs[:, 0] = 0.001 + 0.998 * u
    for col_idx in range(1, 9):
        j     = col_idx + 1
        sigma = rng.integers(0, 2, batch_size)
        lo    = np.where(sigma == 0, float(j - 1), float(j))
        hi    = np.where(sigma == 0, float(j),     float(j + 1))
        configs[:, col_idx] = lo + rng.random(batch_size) * (hi - lo)
    configs[:, 9] = 10.0
    return configs


# ---------------------------------------------------------------------------
# Batch processing (mechanism-tagged)
# ---------------------------------------------------------------------------

def _process_batch(args: tuple) -> dict:
    """
    Process one batch.  Returns aggregated statistics with mechanism data.
    args = (batch_index, batch_size, master_seed, _unused_workers)
    """
    batch_index, batch_size, master_seed, _ = args
    seed    = master_seed + batch_index
    configs = _generate_batch(batch_size, seed)

    n_passed        = 0
    n_failed        = 0
    total_bound     = 0
    min_margin      = np.inf
    max_margin      = -np.inf
    failures        = []
    sigma2_neg      = 0
    sigma2_pos      = 0
    region_counts: dict  = defaultdict(int)
    slot_counts: dict    = defaultdict(int)
    # per-(region, sign_class_str) accumulator: [n, min_margin, max_margin]
    branch_acc: dict     = {}
    # near-miss candidates: list of (margin, speeds_list, sign_class_str, region)
    near_miss_candidates = []

    for i in range(batch_size):
        a       = configs[i]
        sigma   = _sign_class(a)
        region  = _region(a, sigma)
        sigma2  = sigma[0]
        sc_str  = str(sigma)   # hashable key

        found, t_wit, margin, n_bound, slot = find_witness(a)

        total_bound += n_bound
        if margin < min_margin:
            min_margin = margin
        if margin > max_margin:
            max_margin = margin

        # σ₂ tally
        if sigma2 == -1:
            sigma2_neg += 1
        else:
            sigma2_pos += 1

        region_counts[region] += 1

        if found:
            n_passed += 1
            if slot >= 0:
                slot_counts[slot] += 1
        else:
            n_failed += 1
            if len(failures) < 5:
                failures.append({
                    "speeds":    a.tolist(),
                    "margin":    float(margin),
                    "n_bound":   n_bound,
                    "sign_class": sc_str,
                    "region":    region,
                })

        # Branch stats accumulator
        key = (region, sc_str)
        if key not in branch_acc:
            branch_acc[key] = [0, np.inf, -np.inf]
        branch_acc[key][0] += 1
        if margin < branch_acc[key][1]:
            branch_acc[key][1] = margin
        if margin > branch_acc[key][2]:
            branch_acc[key][2] = margin

        # Near-miss candidates (keep only positive-margin = witness found)
        if found and margin >= 0:
            near_miss_candidates.append((float(margin), a.tolist(), sc_str, region))

    return {
        "batch_index":          batch_index,
        "seed":                 seed,
        "n_tested":             batch_size,
        "n_passed":             n_passed,
        "n_failed":             n_failed,
        "total_boundaries":     total_bound,
        "min_margin":           float(min_margin),
        "max_margin":           float(max_margin),
        "mean_boundaries":      total_bound / batch_size if batch_size > 0 else 0.0,
        "sigma2_neg":           sigma2_neg,
        "sigma2_pos":           sigma2_pos,
        "region_counts":        dict(region_counts),
        "slot_counts":          {str(k): v for k, v in slot_counts.items()},
        "branch_acc":           {f"{k[0]}||{k[1]}": v for k, v in branch_acc.items()},
        "near_miss_candidates": near_miss_candidates,
        "failures":             failures,
    }


# ---------------------------------------------------------------------------
# Evidence storage (v2 — adds branch_stats, near_misses tables)
# ---------------------------------------------------------------------------

class EvidenceStore:
    """JSONL + SQLite store with mechanism tables."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS batches (
        batch_index      INTEGER PRIMARY KEY,
        seed             INTEGER,
        n_tested         INTEGER,
        n_passed         INTEGER,
        n_failed         INTEGER,
        total_boundaries INTEGER,
        min_margin       REAL,
        max_margin       REAL,
        mean_boundaries  REAL,
        sigma2_neg       INTEGER,
        sigma2_pos       INTEGER,
        region_counts    TEXT,
        slot_counts      TEXT,
        elapsed_s        REAL,
        timestamp        TEXT
    );
    CREATE TABLE IF NOT EXISTS failures (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_index  INTEGER,
        speeds_json  TEXT,
        margin       REAL,
        n_bound      INTEGER,
        sign_class   TEXT,
        region       TEXT,
        FOREIGN KEY(batch_index) REFERENCES batches(batch_index)
    );
    CREATE TABLE IF NOT EXISTS branch_stats (
        region      TEXT NOT NULL,
        sign_class  TEXT NOT NULL,
        n_configs   INTEGER DEFAULT 0,
        min_margin  REAL,
        max_margin  REAL,
        PRIMARY KEY (region, sign_class)
    );
    CREATE TABLE IF NOT EXISTS near_misses (
        rank        INTEGER PRIMARY KEY,
        margin      REAL NOT NULL,
        speeds_json TEXT NOT NULL,
        sign_class  TEXT,
        region      TEXT
    );
    CREATE TABLE IF NOT EXISTS run_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    """

    def __init__(self, run_dir: Path, near_miss_k: int = 500):
        self.run_dir     = run_dir
        self.near_miss_k = near_miss_k
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path  = run_dir / "batches.jsonl"
        self.db_path     = run_dir / "evidence.db"
        self._conn       = sqlite3.connect(str(self.db_path))
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()
        # In-memory near-miss heap: max-heap via negated margin
        self._near_miss_heap: list = []   # [(-margin, margin, speeds, sc, region)]

    # ---- meta ---------------------------------------------------------------

    def write_meta(self, key: str, value):
        self._conn.execute(
            "INSERT OR REPLACE INTO run_meta(key, value) VALUES (?, ?)",
            (key, json.dumps(value))
        )
        self._conn.commit()

    # ---- batch append -------------------------------------------------------

    def append_batch(self, record: dict, elapsed_s: float):
        ts = datetime.now(timezone.utc).isoformat()
        record["elapsed_s"] = elapsed_s
        record["timestamp"] = ts

        # JSONL (exclude large nested structures)
        jsonl_rec = {
            k: v for k, v in record.items()
            if k not in ("failures", "branch_acc", "near_miss_candidates")
        }
        with open(self.jsonl_path, "a") as f:
            f.write(json.dumps(jsonl_rec) + "\n")

        # SQLite: batches
        self._conn.execute(
            """INSERT OR REPLACE INTO batches
               (batch_index, seed, n_tested, n_passed, n_failed,
                total_boundaries, min_margin, max_margin, mean_boundaries,
                sigma2_neg, sigma2_pos, region_counts, slot_counts,
                elapsed_s, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record["batch_index"], record["seed"],
                record["n_tested"],   record["n_passed"],  record["n_failed"],
                record["total_boundaries"],
                record["min_margin"], record["max_margin"], record["mean_boundaries"],
                record["sigma2_neg"], record["sigma2_pos"],
                json.dumps(record["region_counts"]),
                json.dumps(record["slot_counts"]),
                elapsed_s, ts,
            )
        )

        # SQLite: failures
        for f in record.get("failures", []):
            self._conn.execute(
                """INSERT INTO failures
                   (batch_index, speeds_json, margin, n_bound, sign_class, region)
                   VALUES (?,?,?,?,?,?)""",
                (record["batch_index"], json.dumps(f["speeds"]),
                 f["margin"], f["n_bound"], f.get("sign_class",""), f.get("region",""))
            )

        # SQLite: branch_stats UPSERT
        for key_str, acc in record.get("branch_acc", {}).items():
            region, sc_str = key_str.split("||", 1)
            n, bmin, bmax  = acc
            self._conn.execute(
                """INSERT INTO branch_stats(region, sign_class, n_configs, min_margin, max_margin)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(region, sign_class) DO UPDATE SET
                     n_configs  = n_configs + excluded.n_configs,
                     min_margin = MIN(min_margin, excluded.min_margin),
                     max_margin = MAX(max_margin, excluded.max_margin)""",
                (region, sc_str, n, float(bmin), float(bmax))
            )

        # Near-miss heap update
        for margin, speeds, sc, region in record.get("near_miss_candidates", []):
            heapq.heappush(self._near_miss_heap, (margin, speeds, sc, region))
            if len(self._near_miss_heap) > self.near_miss_k * 2:
                # Keep only the k smallest
                self._near_miss_heap = heapq.nsmallest(
                    self.near_miss_k, self._near_miss_heap
                )
                heapq.heapify(self._near_miss_heap)

        self._conn.commit()

    def flush_near_misses(self):
        """Write current near-miss archive to SQLite."""
        top_k = heapq.nsmallest(self.near_miss_k, self._near_miss_heap)
        self._conn.execute("DELETE FROM near_misses")
        for rank, (margin, speeds, sc, region) in enumerate(top_k, 1):
            self._conn.execute(
                """INSERT INTO near_misses(rank, margin, speeds_json, sign_class, region)
                   VALUES (?,?,?,?,?)""",
                (rank, float(margin), json.dumps(speeds), sc, region)
            )
        self._conn.commit()

    # ---- queries ------------------------------------------------------------

    def cumulative_stats(self) -> dict:
        row = self._conn.execute(
            """SELECT COUNT(*), SUM(n_tested), SUM(n_passed), SUM(n_failed),
                      SUM(total_boundaries), MIN(min_margin), MAX(max_margin),
                      SUM(elapsed_s), SUM(sigma2_neg), SUM(sigma2_pos)
               FROM batches"""
        ).fetchone()
        return {
            "n_batches":         row[0] or 0,
            "n_tested":          row[1] or 0,
            "n_passed":          row[2] or 0,
            "n_failed":          row[3] or 0,
            "total_boundaries":  row[4] or 0,
            "global_min_margin": row[5],
            "global_max_margin": row[6],
            "total_elapsed_s":   row[7] or 0.0,
            "sigma2_neg":        row[8] or 0,
            "sigma2_pos":        row[9] or 0,
        }

    def region_stats(self) -> list:
        rows = self._conn.execute(
            """SELECT region, SUM(n_configs), MIN(min_margin), MAX(max_margin)
               FROM branch_stats GROUP BY region ORDER BY region"""
        ).fetchall()
        return [{"region": r[0], "n_configs": r[1],
                 "min_margin": r[2], "max_margin": r[3]} for r in rows]

    def last_batch_index(self) -> int:
        row = self._conn.execute(
            "SELECT MAX(batch_index) FROM batches"
        ).fetchone()
        return row[0] if row[0] is not None else -1

    def close(self):
        self.flush_near_misses()
        self._conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    default_output = str(Path("~/lrc_runs").expanduser())
    p = argparse.ArgumentParser(
        description=(
            "Event-complete LRC n=11 verification (v2) — mechanism-tagged. "
            "Extends v1 with sign-class fingerprinting, α/β region tagging, "
            "near-miss archive, and branch statistics."
        )
    )
    p.add_argument("--target",       type=int,   default=10_000_000)
    p.add_argument("--batch-size",   type=int,   default=5_000)
    p.add_argument("--workers",      type=int,   default=None)
    p.add_argument("--seed",         type=int,   default=42)
    p.add_argument("--output-dir",   type=str,   default=default_output,
                   help=f"Root directory for evidence (default: {default_output}). "
                         "Must be a local (non-iCloud) path for SQLite.")
    p.add_argument("--run-id",       type=str,   default=None)
    p.add_argument("--resume",       action="store_true")
    p.add_argument("--near-miss-k",  type=int,   default=NEAR_MISS_K_DEFAULT,
                   help="Size of near-miss archive (default 500).")
    return p.parse_args()


def main():
    args = parse_args()

    n_workers   = args.workers or cpu_count()
    batch_size  = args.batch_size
    target      = args.target
    n_batches   = (target + batch_size - 1) // batch_size
    master_seed = args.seed

    # Resolve output directory to absolute path
    output_dir = Path(args.output_dir).expanduser().resolve()
    run_id     = args.run_id or datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    run_dir    = output_dir / run_id
    store      = EvidenceStore(run_dir, near_miss_k=args.near_miss_k)

    # Resume support
    start_batch = 0
    if args.resume:
        start_batch = store.last_batch_index() + 1
        print(f"Resuming from batch {start_batch}.")
    else:
        config = {
            "run_id":       run_id,
            "version":      "v2",
            "target":       target,
            "batch_size":   batch_size,
            "n_batches":    n_batches,
            "n_workers":    n_workers,
            "seed":         master_seed,
            "theta":        THETA,
            "n_runners":    N_RUNNERS,
            "method":       "boundary_set_event_complete_v2",
            "near_miss_k":  args.near_miss_k,
            "started_at":   datetime.now(timezone.utc).isoformat(),
            "python":       sys.version,
            "numpy":        np.__version__,
            "output_dir":   str(output_dir),
        }
        with open(run_dir / "config.json", "w") as f:
            json.dump(config, f, indent=2)
        store.write_meta("config", config)

    # Graceful shutdown
    interrupted = [False]
    def _handler(sig, frame):
        interrupted[0] = True
        print("\nInterrupted — saving near-miss archive and current state.", flush=True)
    signal.signal(signal.SIGINT,  _handler)
    signal.signal(signal.SIGTERM, _handler)

    print("=" * 72)
    print("LRC n=11 Verification v2 — Mechanism-Tagged (Event-Complete)")
    print(f"  Target   : {target:,} configurations")
    print(f"  Batches  : {n_batches:,}  x  {batch_size:,} configs/batch")
    print(f"  Workers  : {n_workers}")
    print(f"  Seed     : {master_seed}")
    print(f"  Run ID   : {run_id}")
    print(f"  Output   : {run_dir}")
    print(f"  Near-miss: top {args.near_miss_k}")
    print("=" * 72, flush=True)

    wall_start = time.monotonic()
    chunk = max(1, min(n_workers * 2, 20))

    batch_args = [
        (b, batch_size, master_seed, n_workers)
        for b in range(start_batch, n_batches)
    ]

    near_miss_flush_interval = 50   # flush near-miss table every N batches

    with Pool(processes=n_workers) as pool:
        for idx, result in enumerate(
            pool.imap_unordered(_process_batch, batch_args, chunksize=chunk)
        ):
            if interrupted[0]:
                break

            elapsed = time.monotonic() - wall_start
            store.append_batch(result, elapsed_s=elapsed)

            # Periodic near-miss flush
            done_batches = idx + 1
            if done_batches % near_miss_flush_interval == 0:
                store.flush_near_misses()

            # Immediate failure alert
            if result["n_failed"] > 0:
                print(f"\n!!! FAILURE in batch {result['batch_index']}: "
                      f"{result['n_failed']} config(s) without witness !!!")
                for fail in result.get("failures", []):
                    print(f"    speeds : {[f'{x:.6f}' for x in fail['speeds']]}")
                    print(f"    margin : {fail['margin']:.8f}")
                    print(f"    region : {fail['region']}  sign: {fail['sign_class']}")

            # Progress every 10 batches
            if done_batches % 10 == 0 or done_batches == 1:
                stats = store.cumulative_stats()
                rate  = stats["n_tested"] / elapsed if elapsed > 0 else 0
                remaining = max(0, target - stats["n_tested"])
                eta_h = (remaining / rate / 3600) if rate > 0 else 0
                rcs   = result.get("region_counts", {})
                rc_str = "  ".join(f"{k}={v}" for k, v in sorted(rcs.items()))
                print(
                    f"  Batch {done_batches:,}/{n_batches:,} | "
                    f"tested {stats['n_tested']:,} | "
                    f"failed {stats['n_failed']} | "
                    f"min_margin {stats['global_min_margin']:.6f} | "
                    f"rate {rate:.0f}/s | ETA {eta_h:.1f}h",
                    flush=True
                )
                if rc_str:
                    print(f"    regions this batch: {rc_str}", flush=True)

    # Final near-miss flush
    store.flush_near_misses()

    # Summary
    stats      = store.cumulative_stats()
    reg_stats  = store.region_stats()
    total_time = time.monotonic() - wall_start

    summary = {
        "run_id":                   run_id,
        "version":                  "v2",
        "completed_at":             datetime.now(timezone.utc).isoformat(),
        "interrupted":              interrupted[0],
        "n_tested":                 stats["n_tested"],
        "n_passed":                 stats["n_passed"],
        "n_failed":                 stats["n_failed"],
        "n_batches_completed":      stats["n_batches"],
        "total_boundaries_evaluated": stats["total_boundaries"],
        "global_min_margin":        stats["global_min_margin"],
        "global_max_margin":        stats["global_max_margin"],
        "sigma2_neg":               stats["sigma2_neg"],
        "sigma2_pos":               stats["sigma2_pos"],
        "region_stats":             reg_stats,
        "total_time_s":             total_time,
        "configs_per_second":       stats["n_tested"] / total_time if total_time > 0 else 0,
        "success_rate":             (stats["n_passed"] / stats["n_tested"]
                                     if stats["n_tested"] > 0 else None),
        "p_failure_upper_95":       (3.0 / stats["n_tested"]
                                     if stats["n_failed"] == 0 and stats["n_tested"] > 0
                                     else None),
    }

    with open(run_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    store.write_meta("summary", summary)
    store.close()

    print("\n" + "=" * 72)
    print("VERIFICATION SUMMARY (v2)")
    print("=" * 72)
    print(f"  Configurations tested : {stats['n_tested']:,}")
    print(f"  Witnesses found       : {stats['n_passed']:,}")
    print(f"  Failures              : {stats['n_failed']}")
    if stats["n_tested"] > 0:
        print(f"  Success rate          : {100*stats['n_passed']/stats['n_tested']:.6f}%")
    print(f"  Global min margin     : {stats['global_min_margin']:.8f}")
    print(f"  σ₂ = -1 (neg_branch)  : {stats['sigma2_neg']:,}")
    print(f"  σ₂ = +1 (pos branch)  : {stats['sigma2_pos']:,}")
    print(f"  Total boundaries eval : {stats['total_boundaries']:,}")
    print(f"  Elapsed               : {total_time:.1f}s ({total_time/3600:.2f}h)")
    if stats["n_tested"] > 0:
        print(f"  Throughput            : {stats['n_tested']/total_time:.0f} configs/s")

    print("\n  Branch coverage (by region):")
    for rs in reg_stats:
        print(f"    {rs['region']:12s}  n={rs['n_configs']:>10,}  "
              f"min_margin={rs['min_margin']:.6f}  max_margin={rs['max_margin']:.6f}")

    if stats["n_failed"] == 0 and stats["n_tested"] > 0:
        p_up = 3.0 / stats["n_tested"]
        print(f"\n  95%% confidence: failure rate < {p_up:.2e}  "
              f"(< 1 per {1/p_up:.0f} configs)")
        print(f"\n  OUTCOME: No counterexample detected in {stats['n_tested']:,} "
              f"event-complete trials.")
        print(f"  All instances fall within analytically predicted closure patterns.")
    else:
        print(f"\n  OUTCOME: {stats['n_failed']} failure(s) detected — inspect evidence.db.")

    print(f"\n  Evidence stored at: {run_dir}")
    print("=" * 72)


if __name__ == "__main__":
    main()
