# Computational Verification — Lonely Runner Conjecture, n = 11

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19341800.svg)](https://doi.org/10.5281/zenodo.19341800)

This repository contains the computational support for the paper:

> **"Eleven Lonely Runners: Complementary Pairs and the Coupling Principle"**
> Trent Palelei, MI Research Foundation, 2026
> DOI: [10.5281/zenodo.19341800](https://doi.org/10.5281/zenodo.19341800)

The analytic proof is complete and self-contained. The computations here are
**not part of the proof**. They provide large-scale, independent, mechanism-level
validation of the proof's structural claims, organised so that a referee or
third party can reproduce every result from scratch.

---

## Summary of Campaigns

Two independent verification campaigns were conducted on an Apple M4 MacBook Pro
(10-core CPU, Python 3.14, NumPy), using different random seeds to ensure
independence.

| Campaign | Seed | Configurations | Failures | Boundaries evaluated | Runtime | 95% CI upper bound |
|---|---|---|---|---|---|---|
| `run_1e8_mech` | 42 | 100,000,000 | **0** | 61,359,721,983 | 65 min | < 3 × 10⁻⁸ |
| `run_1e9_mech` | 1337 | 1,000,000,000 | **0** | 613,652,841,085 | 10.7 hr | < 3 × 10⁻⁹ |
| **Combined** | — | **1,100,000,000** | **0** | **674,012,563,068** | — | **< 6.9 × 10⁻⁹ (99.9%)** |

Zero failures across 1.1 billion event-complete trials and 674 billion boundary
evaluations. Every instance fell into one of the analytically predicted closure
patterns.

---

## What Is Verified

### 1. Exact Symbolic Verification (`results/symbolic/`)

All 34 explicit rational inequalities in the paper verified with Python's
`fractions.Fraction` — **exact arithmetic, zero floating-point error**.

Checks include:
- Modular inverses mod 11 (complementary pairs structure)
- Slot boundaries I₁ through I₉
- Chain A gap bound (= 1/50 exactly)
- Chain B incompatibility (a₇ min > a₇ max by 1575/218 − 560/87 > 0)
- Negative-branch sub-gap (= 538/54395 exactly)
- α/β decomposition boundaries (Lemma 6.2)
- W_ref closure condition (interval length > 7)
- Runner 2 elimination bounds

**Result: 34/34 PASS**. See `results/symbolic/exact_symbolic_results.json`.

### 2. Boundary-Set Witness Search (the two main campaigns)

For each random configuration in the commensurable large-ratio regime, the
verifier uses the **event-complete boundary-set method** to search for a
witness time t* in the isolation window W_ref = [1/(11·a₁), 10/(11·a₁)].

**Method guarantee:** Unlike a time-grid search, the boundary-set method is
mathematically complete within W_ref — it evaluates at every time where any
runner's distance to the origin crosses the 1/11 threshold, so no witness can
be missed between evaluation points.

### 3. Mechanism Tagging (branch statistics)

Each configuration is classified into the proof's analytic regions:

| Region | Paper section | Configs (10⁹ run) |
|---|---|---|
| `neg_branch` (σ₂ = −1) | §6.1 | 500,010,482 |
| `alpha_1` (σ₂ = +1, σ₉ = −1, a₉ < 580/69) | §6.2 α₁ | 101,435,772 |
| `alpha_1e` (σ₂ = +1, σ₉ = −1, a₉ ~ 9) | §6.2 α₁ ext. | 2,286,847 |
| `alpha_3` (σ₂ = +1, σ₉ = +1, a₉ < 1960/207) | §6.2 α₃ | 117,263,159 |
| `beta_1` (σ₂ = +1, σ₉ = −1, a₉ ∈ [580/69, 87/10)) | §6.2 β₁ | 73,524,365 |
| `beta_2` (σ₂ = +1, σ₉ = −1, a₉ ∈ [87/10, 980/109)) | §6.2 β₂ | 72,713,310 |
| `beta_3` (σ₂ = +1, σ₉ = +1, a₉ ∈ [1960/207, 86/9]) | §6.2 β₃ | 21,737,660 |
| `beta_4` (σ₂ = +1, σ₉ = +1, a₉ ∈ (86/9, 10)) | §6.2 β₄ | 111,028,405 |

All regions covered. No exceptional behaviour observed in any region.

---

## Configuration Space Tested

- **a₁ ∈ (0, 1)**: the large-ratio commensurable regime (p₁₀/p₁ > 10),
  biased toward small a₁ to stress-test the W_ref window geometry
- **aⱼ ∈ (j−1, j) or (j, j+1)**: random sign class for each runner j = 2..9,
  uniform within the sign-range interval (sign-range containment, Lemma 5.1)
- **a₁₀ = 10**: normalised (WLOG)
- **W_ref = [1/(11·a₁), 10/(11·a₁)]**: isolation window (runner 1 is
  automatically safe here; proof checks runners 2–10)

---

## Reproduction

Requirements: Python ≥ 3.10, NumPy, SQLite3 (stdlib).

```bash
# Clone
git clone https://github.com/trentjp-gecta9/lrc-n11-computational-verification
cd lrc-n11-computational-verification

# Smoke test — 30 seconds
python3 scripts/verify_lrc_n11.py \
  --target 5000 --batch-size 500 --workers 4 \
  --seed 99 --run-id smoke_repro

# Reproduce 10^8 campaign (seed 42, ~65 min on M4 10-core)
python3 scripts/verification_monitor.py \
  --run-id run_1e8_repro --target 100000000 \
  --seed 42 --output-dir ~/lrc_runs_repro

# Reproduce 10^9 campaign (seed 1337, ~11 hr on M4 10-core)
python3 scripts/verification_monitor.py \
  --run-id run_1e9_repro --target 1000000000 \
  --seed 1337 --output-dir ~/lrc_runs_repro

# Exact symbolic verification (seconds, exact arithmetic)
python3 scripts/exact_symbolic.py
```

Runs are **resumable**: if interrupted, re-run the same command and it picks
up from the last checkpoint automatically.

---

## Repository Structure

```
├── README.md                          this file
├── MANIFEST.json                      provenance registry + run metadata
├── scripts/
│   ├── verify_lrc_n11.py          main boundary-set verification harness
│   ├── exact_symbolic.py             exact rational inequality checks (34 claims)
│   ├── analyze_results.py            query evidence DB, print summary tables
│   ├── verification_monitor.py           autonomous monitor + auto-restarter
│   └── launch_campaign.py            auto-launches next campaign on completion
├── results/
│   ├── symbolic/
│   │   └── exact_symbolic_results.json    34/34 symbolic checks
│   ├── run_1e8_mech/
│   │   ├── summary.json              final statistics
│   │   ├── config.json               run parameters (seed, workers, etc.)
│   │   └── batches.jsonl             full audit trail (20,000 batches, 10 MB)
│   └── run_1e9_mech/
│       ├── summary.json              final statistics
│       ├── config.json               run parameters (seed 1337)
│       └── batches.jsonl.gz          full audit trail compressed (200,000 batches, 15 MB)
```

---

## Statistical Interpretation

With N trials and 0 failures, the 95% confidence upper bound on the true
failure rate p is approximately 3/N (rule of three). For the combined campaign:

- N = 1,100,000,000 → p < **2.7 × 10⁻⁹** (95% confidence)
- Equivalently: 99.9% confidence that p < **6.9 × 10⁻⁹**

This means the expected number of undetected failures in 1.1 billion trials,
if the true rate were at the 99.9% bound, would be less than 7.6 — implying
no such failures were hidden by sampling luck.

**This computation is not part of the proof.** The theorem is established
analytically in the paper. The computation provides independent, large-scale
validation that the proof's structural mechanisms behave exactly as claimed,
with no hidden exceptional regimes detected below 10⁹.

---

## Hardware and Software

| Item | Value |
|---|---|
| Hardware | Apple M4 MacBook Pro, 10-core CPU |
| OS | macOS |
| Python | 3.14 |
| NumPy | (standard) |
| Parallelism | `multiprocessing.Pool`, 10 workers |
| Arithmetic | IEEE 754 float64; boundary times exact via rational construction |
| Symbolic checks | `fractions.Fraction` (exact, no floating-point) |

---

## Contact

Trent Palelei
GitHub: [@trentjp-gecta9](https://github.com/trentjp-gecta9)
ORCID: [0009-0002-8692-2833](https://orcid.org/0009-0002-8692-2833)
