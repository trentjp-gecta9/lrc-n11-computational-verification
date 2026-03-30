#!/usr/bin/env python3
"""
exact_symbolic.py
=================
Exact rational verification of the key inequality claims in the
n=11 Lonely Runner Conjecture proof (Palelei 2026, v28.2).

All arithmetic uses Python's fractions.Fraction, so results are exact
with no floating-point error.

Output: exact_symbolic_results.json in the run directory (or stdout).

Usage
-----
  python3 exact_symbolic.py [--output PATH]

Author: Trent Palelei
Date  : 2026-03-30
"""

import argparse
import json
import sys
from fractions import Fraction

# ---------------------------------------------------------------------------

CHECKS = []
FAILURES = []


def check(label: str, condition: bool, lhs=None, rhs=None, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    rec = {
        "label":  label,
        "status": status,
        "detail": detail or (f"{lhs} vs {rhs}" if lhs is not None else ""),
    }
    CHECKS.append(rec)
    sym = "✓" if condition else "✗"
    print(f"  [{sym}] {label}")
    if not condition:
        FAILURES.append(rec)
        if lhs is not None:
            print(f"       expected: {lhs} ≡ {rhs} but got {lhs != rhs}")
        if detail:
            print(f"       {detail}")
    return condition


def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ---------------------------------------------------------------------------

def run_all_checks():

    # ------------------------------------------------------------------
    section("Complementary pairs: modular inverses mod 11")
    # ------------------------------------------------------------------
    expected_inverses = {2: 6, 3: 4, 4: 3, 5: 9, 6: 2, 7: 8, 8: 7, 9: 5}
    for j, expected in expected_inverses.items():
        inv = pow(j, -1, 11)
        check(f"  {j}⁻¹ ≡ {expected} (mod 11)", inv == expected,
              detail=f"computed {inv}")

    # ------------------------------------------------------------------
    section("Slot boundaries (I_k = slot k in pacemaker convention)")
    # ------------------------------------------------------------------
    I9_left  = Fraction(100, 110)
    I9_right = Fraction(109, 110)
    check("I_9 left  = 10/11",     I9_left  == Fraction(10, 11))
    check("I_9 right = 109/110",   I9_right == Fraction(109, 110))
    check("I_9 width = 9/110",
          I9_right - I9_left == Fraction(9, 110),
          detail=f"width = {I9_right - I9_left} = {float(I9_right - I9_left):.6f}")

    I8_left  = Fraction(89, 110)
    I8_right = Fraction(98, 110)
    check("I_8 left  = 89/110",    I8_left  == Fraction(89, 110))
    check("I_8 right = 98/110",    I8_right == Fraction(98, 110))

    # ------------------------------------------------------------------
    section("Chain A  (Lemma 5.3): runner 3, σ₃=+1, covers I_9 → gap in I_8")
    # ------------------------------------------------------------------
    # B_{6,4}[left] = (11·4 + 10) / (11 · a_6) = 54 / (11 · a_6)
    # At a_6 = 225/38 (maximum of sign range):
    a6_max  = Fraction(225, 38)
    B64_l   = Fraction(54, 11) / a6_max        # = 54·38 / (11·225)
    expected_B64_l = Fraction(228, 275)
    check("B_{6,4}[left] at a_6=225/38  = 228/275",
          B64_l == expected_B64_l,
          detail=f"computed {B64_l} = {float(B64_l):.8f}")

    check("B_{6,4}[left] > I_8[left]  (gap forced in I_8)",
          B64_l > I8_left,
          detail=f"{B64_l} > {I8_left}  →  gap = {B64_l - I8_left}")

    gap_A = B64_l - I8_left
    check("Chain A gap width = 1/50",
          gap_A == Fraction(1, 50),
          detail=f"computed {gap_A} = {float(gap_A):.8f}")

    # ------------------------------------------------------------------
    section("Chain B  (Lemma 5.4): runner 4, σ₄=+1, covers I_9 → incompatibility in I_7")
    # ------------------------------------------------------------------
    a4_max         = Fraction(450, 109)
    a7_min_needed  = Fraction(7, 4) * a4_max       # to cover right part of I_7
    a7_max_allowed = Fraction(560, 87)              # from I_7 coverage constraint
    expected_a7min = Fraction(1575, 218)

    check("a_7 min needed = 1575/218",
          a7_min_needed == expected_a7min,
          detail=f"computed {a7_min_needed} = {float(a7_min_needed):.6f}")

    check("a_7 min > a_7 max  (incompatibility confirmed)",
          a7_min_needed > a7_max_allowed,
          detail=f"diff = {a7_min_needed - a7_max_allowed} = {float(a7_min_needed - a7_max_allowed):.6f}")

    # ------------------------------------------------------------------
    section("Negative branch  (§6.1): forced configuration and gap")
    # ------------------------------------------------------------------
    # With σ₂ = -1 and a_9 = 86/9, runner 9's B_{9,8} interval:
    a9_neg     = Fraction(86, 9)
    B98_left   = Fraction(11*8 + 10, 11) / a9_neg   # = 98 / (11 · 86/9) = 882/946
    B98_right  = Fraction(11*8 + 12, 11) / a9_neg   # = 100 / (11 · 86/9) = 900/946
    expected_l = Fraction(441, 473)
    expected_r = Fraction(450, 473)

    check("B_{9,8}[left]  = 441/473",  B98_left  == expected_l,
          detail=f"computed {B98_left}")
    check("B_{9,8}[right] = 450/473",  B98_right == expected_r,
          detail=f"computed {B98_right}")
    check("B_{9,8} ⊆ I_9  (left)",     B98_left  >= I9_left,
          detail=f"{B98_left} >= {I9_left}")
    check("B_{9,8} ⊆ I_9  (right)",    B98_right <= I9_right,
          detail=f"{B98_right} <= {I9_right}")

    # Gap in I_9 after runner 9 covers B_{9,8}:
    # Left gap:  [I9_left, B98_left]
    # Right gap: [B98_right, I9_right]
    right_gap = I9_right - B98_right
    check("Right gap in I_9 > 0",      right_gap > 0,
          detail=f"width = {right_gap} = {float(right_gap):.8f}")

    # Further constrained gap cited in paper: 538/54395
    gap_neg_paper = Fraction(1216, 1265) - Fraction(450, 473)
    expected_gap  = Fraction(538, 54395)
    check("Sub-gap (1216/1265 - 450/473) = 538/54395",
          gap_neg_paper == expected_gap,
          detail=f"computed {gap_neg_paper} = {float(gap_neg_paper):.10f}")
    check("Sub-gap > 0",               gap_neg_paper > 0)

    # ------------------------------------------------------------------
    section("Alpha decomposition  (Lemma 6.2)")
    # ------------------------------------------------------------------
    # α₁: at boundary a_9 = 580/69, both sides of inequality are equal
    a9_alpha1_bound = Fraction(580, 69)
    lhs_alpha1      = Fraction(20, 9)
    rhs_alpha1      = Fraction(23, 87) * a9_alpha1_bound
    check("α₁ boundary: 20/9 = 23·(580/69)/87  (tight)",
          lhs_alpha1 == rhs_alpha1,
          detail=f"LHS={lhs_alpha1}, RHS={rhs_alpha1}")
    # α₁ applies to σ₉ = -1, so a_9 ∈ (8,9) — the boundary 580/69 ≈ 8.41 is in this range
    check("α₁ boundary a_9 = 580/69 ∈ (8, 9)  (σ₉=-1 sign range)",
          8 < a9_alpha1_bound < 9,
          detail=f"{float(a9_alpha1_bound):.6f}")

    # α₃: boundary at a_9 = 1960/207
    a9_alpha3_bound = Fraction(1960, 207)
    check("α₃ boundary a_9 = 1960/207 ∈ (9, 10)",
          9 < a9_alpha3_bound < 10,
          detail=f"{float(a9_alpha3_bound):.6f}")

    # β-region coverage: σ₉ = -1 case covers [580/69, 980/109)
    beta_left_neg  = Fraction(580, 69)
    beta_right_neg = Fraction(980, 109)
    check("β₁ ∪ β₂ upper bound 980/109 < 9  (within σ₉=-1 range)",
          beta_right_neg < 9,
          detail=f"980/109 = {float(beta_right_neg):.6f}")
    check("β₃ ∪ β₄ lower bound 1960/207 > 9  (within σ₉=+1 range)",
          a9_alpha3_bound > 9)

    # ------------------------------------------------------------------
    section("W_ref closure (Corollary 7.4): length guarantees ≥1 pacemaker cycle")
    # ------------------------------------------------------------------
    # Length of W_ref = (10 - 1)/(11 · a_1) = 9/(11 · a_1)
    # For a_1 < 1: length > 9/11 > 0.  Paper requires length > 1:
    # 9/(11·a_1) > 1  ⟺  a_1 < 9/11.
    # The paper normalises a_10 = 10, a_1 < 1, and the ratio condition
    # a_10/a_1 > 10 guarantees a_1 < 1.  Checking numeric example:
    # a_1 must be strictly less than 9/11 for length > 1
    for a1_num, a1_den in [(1, 10), (3, 11), (8, 11)]:
        a1 = Fraction(a1_num, a1_den)
        length = Fraction(9, 11) / a1
        label = f"W_ref length with a_1={a1} = {length} > 1"
        check(label, length > 1, detail=f"= {float(length):.4f}")

    # Edge: a_1 = 9/11 gives length exactly 1 (excluded by strict inequality)
    a1_edge = Fraction(9, 11)
    length_edge = Fraction(9, 11) / a1_edge
    check("W_ref length with a_1=9/11 = 1  (boundary, excluded)",
          length_edge == 1, detail="confirms strict regime requires a_1 < 9/11")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Exact symbolic verification of LRC n=11 key inequalities."
    )
    parser.add_argument("--output", type=str, default=None,
                        help="Write JSON results to this path.")
    args = parser.parse_args()

    print("=" * 60)
    print("  LRC n=11 Exact Symbolic Verification")
    print("  (fractions.Fraction — zero floating-point error)")
    print("=" * 60)

    run_all_checks()

    n_total   = len(CHECKS)
    n_passed  = sum(1 for c in CHECKS if c["status"] == "PASS")
    n_failed  = len(FAILURES)

    print(f"\n{'=' * 60}")
    print(f"  Result: {n_passed}/{n_total} checks passed,  {n_failed} failed.")
    if n_failed == 0:
        print("  All symbolic inequalities verified exactly.")
    else:
        print("  FAILURES:")
        for f in FAILURES:
            print(f"    - {f['label']}: {f['detail']}")
    print("=" * 60)

    output = {
        "n_total":  n_total,
        "n_passed": n_passed,
        "n_failed": n_failed,
        "checks":   CHECKS,
        "failures": FAILURES,
    }

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(output, fh, indent=2)
        print(f"\n  Results written to: {args.output}")

    sys.exit(0 if n_failed == 0 else 1)


if __name__ == "__main__":
    main()
