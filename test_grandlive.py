"""Cross-checks on the Grand Live song catalogue and the run tracker.

The song costs are curated by hand, so they are validated against two figures
published independently of each other. Run: python test_grandlive.py
"""

from __future__ import annotations

import sys

import grandlive_data as gd
from grandlive import RunState


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{('  -- ' + detail) if detail and not ok else ''}")
    return ok


def main() -> int:
    ok = True
    lesson_songs = [s for s in gd.SONGS if not s.special]

    # 1. Column totals must match GameTora's published "total cost of all songs".
    total = gd.requirement(lesson_songs)
    ok &= check(
        "token totals match GameTora's published total",
        total == gd.PUBLISHED_TOTAL,
        f"{total} vs {gd.PUBLISHED_TOTAL}",
    )

    # 2. Per-Live positive-value sums must match tracentrial's stated coin
    #    requirements. This validates the cost table and the mapping between
    #    the two sites' song names at the same time.
    for live, want in gd.TRACENTRIAL_REQUIREMENTS.items():
        got = gd.requirement(gd.worth_buying(gd.songs_for_live(live)))
        ok &= check(
            f"Live {live} requirement matches tracentrial",
            got == want,
            f"{got} vs {want}",
        )

    # 3. Catalogue sanity.
    ok &= check("23 songs total", len(gd.SONGS) == 23, str(len(gd.SONGS)))
    ok &= check("21 buyable lesson songs", len(lesson_songs) == 21, str(len(lesson_songs)))
    ok &= check("song keys are unique", len({s.key for s in gd.SONGS}) == len(gd.SONGS))
    ok &= check(
        "every lesson song costs something",
        all(s.total_cost > 0 for s in lesson_songs),
    )
    ok &= check(
        "every lesson song is rated",
        all(s.net_value is not None for s in lesson_songs),
    )

    # 4. The pool carries unbought songs forward.
    pool4 = gd.pool_at(4, bought=set())
    ok &= check("unbought songs stay in the pool", len(pool4) == 21, str(len(pool4)))
    pool4b = gd.pool_at(4, bought={"kiseki", "seishun"})
    ok &= check("bought songs leave the pool", len(pool4b) == 19, str(len(pool4b)))
    ok &= check("Live 1 pool has only Live 1 songs", len(gd.pool_at(1, set())) == 8)

    # 5. Run tracker arithmetic.
    run = RunState()
    run.set_tokens({"dance": 100, "passion": 100, "vocal": 100, "visual": 100, "mental": 100})
    before = dict(run.tokens)
    song = gd.BY_KEY["kiseki"]  # 0 / 21 / 0 / 0 / 21
    run.buy_song(song.key)
    ok &= check(
        "buying a song debits the right tokens",
        run.tokens["passion"] == before["passion"] - 21
        and run.tokens["mental"] == before["mental"] - 21
        and run.tokens["dance"] == before["dance"],
        str(run.tokens),
    )
    ok &= check("bought song is recorded", "kiseki" in run.bought)
    ok &= check(
        "bought song leaves the remaining requirement",
        gd.BY_KEY["kiseki"] not in run.remaining_targets(),
    )

    run.take_course({"dance": 10})
    ok &= check("a course debits tokens", run.tokens["dance"] == before["dance"] - 10)
    ok &= check("courses are counted", run.courses_this_live == 1)

    run.advance_live()
    ok &= check("advancing moves to Live 2", run.live == 2)
    ok &= check("course count resets per Live", run.courses_this_live == 0)
    ok &= check("purchases persist across Lives", "kiseki" in run.bought)

    # Tokens may legitimately go negative if you mis-enter; the tracker should
    # report that rather than silently clamping.
    run.set_tokens({t: 5 for t in gd.TOKENS})
    run.buy_song("seishun")  # 0 / 0 / 32 / 0 / 12
    ok &= check("overspend is visible, not clamped", run.tokens["vocal"] == 5 - 32, str(run.tokens))
    ok &= check("overspend is flagged", run.overspent(), "expected overspent() to be True")

    run.reset()
    ok &= check("reset clears the run", run.live == 1 and not run.bought and not any(run.tokens.values()))

    # 6. Undo.
    run2 = RunState()
    run2.set_tokens({t: 100 for t in gd.TOKENS})
    run2.buy_song("zensoku")
    run2.undo()
    ok &= check(
        "undo restores tokens and purchases",
        not run2.bought and run2.tokens["dance"] == 100,
        str(run2.tokens),
    )

    print("\nall good" if ok else "\nfailures above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
