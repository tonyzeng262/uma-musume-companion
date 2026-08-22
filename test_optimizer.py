"""Checks on the team solver. Run: python test_optimizer.py"""

from __future__ import annotations

import itertools
import random
import sys

from optimizer import (
    DEFAULT_CATEGORIES,
    STYLES,
    CardView,
    Weights,
    optimize,
    score_card,
)

GRADES = ("S", "A", "B", "C", "D", "E", "F", "G")
APTS = ("turf", "dirt", "sprint", "mile", "medium", "long", "front", "pace", "late", "end")
STATS = ("speed", "stamina", "power", "guts", "wit")


def make_cards(n: int, seed: int = 0) -> list[CardView]:
    rng = random.Random(seed)
    return [
        CardView(
            card_id=1000 + i,
            name=f"Uma {i}",
            tier=rng.choice([None, "S", "A", "B", "C"]),
            tier_note=None,
            image_url=None,
            aptitude={k: rng.choice(GRADES) for k in APTS},
            max_stats={s: rng.randint(90, 120) for s in STATS},
            growth={s: rng.choice([0, 0, 10, 20]) for s in STATS},
        )
        for i in range(n)
    ]


def brute_force_one_category(cards, cat, w) -> float:
    """Exhaustive best score for a single category, for comparison."""
    best = float("-inf")
    for trio in itertools.combinations(range(len(cards)), 3):
        for styles in itertools.product(STYLES, repeat=3):
            total = sum(score_card(cards[i], cat, s, w)[0] for i, s in zip(trio, styles))
            # Parallel arcs charge 0 / penalty / 2*penalty, so k umas sharing a
            # style cost penalty * (0 + 1 + ... + k-1) = penalty * k(k-1)/2.
            counts = [styles.count(s) for s in set(styles)]
            total -= w.style_penalty * sum(k * (k - 1) // 2 for k in counts)
            best = max(best, total)
    return best


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{('  -- ' + detail) if detail and not ok else ''}")
    return ok


def main() -> int:
    passed = True
    w = Weights()

    # 1. Structural invariants on a realistic-sized roster.
    cards = make_cards(40, seed=1)
    res = optimize(cards, w)
    ids = [a.card_id for a in res.assignments]
    passed &= check("fills all 15 slots", len(res.assignments) == 15, str(len(res.assignments)))
    passed &= check("no uma used twice", len(ids) == len(set(ids)))
    per_cat = {c.key: 0 for c in DEFAULT_CATEGORIES}
    for a in res.assignments:
        per_cat[a.category] += 1
    passed &= check("three per category", set(per_cat.values()) == {3}, str(per_cat))

    # 2. A high style penalty should produce three distinct styles per category.
    strict = optimize(cards, Weights(style_penalty=10_000.0))
    groups: dict[str, list[str]] = {}
    for a in strict.assignments:
        groups.setdefault(a.category, []).append(a.style)
    passed &= check(
        "distinct styles under a high penalty",
        all(len(set(v)) == 3 for v in groups.values()),
        str(groups),
    )

    # 3. Ignoring style should never score worse than enforcing it.
    free = optimize(cards, Weights(style_penalty=0.0))
    raw_free = sum(a.score for a in free.assignments)
    raw_strict = sum(a.score for a in strict.assignments)
    passed &= check(
        "penalty-free team scores at least as high",
        raw_free >= raw_strict - 1e-6,
        f"{raw_free:.2f} vs {raw_strict:.2f}",
    )

    # 4. The real test: min-cost flow must match exhaustive search.
    #    One category keeps brute force tractable (C(9,3) * 4^3 combinations).
    small = make_cards(9, seed=3)
    for pen in (0.0, 60.0, 10_000.0):
        ww = Weights(style_penalty=pen)
        cat = DEFAULT_CATEGORIES[2]
        got = optimize(small, ww, categories=(cat,))
        # Recompute the solver's objective the same way brute force does.
        styles = [a.style for a in got.assignments]
        counts = [styles.count(s) for s in set(styles)]
        obj = sum(a.score for a in got.assignments) - pen * sum(k * (k - 1) // 2 for k in counts)
        want = brute_force_one_category(small, cat, ww)
        passed &= check(
            f"matches brute force (style_penalty={pen:g})",
            abs(obj - want) < 1e-6,
            f"solver {obj:.4f} vs brute force {want:.4f}",
        )

    # 5. Too few umas: report what could not be filled instead of crashing.
    short = optimize(make_cards(4, seed=5), w)
    passed &= check(
        "short roster fills what it can",
        len(short.assignments) == 4 and len(short.unfilled) == 11,
        f"{len(short.assignments)} assigned, {len(short.unfilled)} unfilled",
    )
    passed &= check("empty roster is handled", len(optimize([], w).assignments) == 0)

    # 6. Style restrictions must be respected.
    limited = optimize(cards, w, allowed_styles={c.card_id: {"front"} for c in cards[:5]})
    bad = [a for a in limited.assignments if a.card_id in {c.card_id for c in cards[:5]}
           and a.style != "front"]
    passed &= check("allowed_styles is respected", not bad, str([a.name for a in bad]))

    print("\nall good" if passed else "\nfailures above")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
