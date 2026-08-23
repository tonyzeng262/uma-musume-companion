"""Cross-checks on the Grand Live song catalogue and the run tracker.

The song costs are curated by hand, so they are validated against two figures
published independently of each other. Run: python test_grandlive.py
"""

from __future__ import annotations

import json
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

    # 5. The token names Global actually uses.
    ok &= check("fifth token is Composure, not Mental", gd.TOKENS[4] == "composure")
    ok &= check(
        "token abbreviations are Da/Pa/Vo/Vi/Co",
        [gd.TOKEN_SHORT[t] for t in gd.TOKENS] == ["Da", "Pa", "Vo", "Vi", "Co"],
        str([gd.TOKEN_SHORT[t] for t in gd.TOKENS]),
    )
    ok &= check("every token has a colour", all(t in gd.TOKEN_COLOR for t in gd.TOKENS))

    # 6. Run tracker arithmetic: the entered balance stays put, spending is
    #    tracked against it, and "have now" is the two combined.
    run = RunState()
    ok &= check("no baseline before anything is entered", not run.has_baseline)
    run.set_tokens({t: 100 for t in gd.TOKENS})
    ok &= check("entering a balance sets the baseline", run.has_baseline)

    run.buy_song("kiseki")  # Pa 21, Co 21
    ok &= check(
        "buying a song debits the right tokens",
        run.tokens["passion"] == 79 and run.tokens["composure"] == 79
        and run.tokens["dance"] == 100,
        str(run.tokens),
    )
    ok &= check(
        "the entered figure is not edited in place",
        run.entered == {t: 100 for t in gd.TOKENS},
        str(run.entered),
    )
    ok &= check(
        "spending is tracked separately",
        run.spent_since_entry["passion"] == 21 and run.spent_since_entry["dance"] == 0,
        str(run.spent_since_entry),
    )
    ok &= check("bought song is recorded", "kiseki" in run.bought)
    ok &= check(
        "bought song leaves the remaining requirement",
        gd.BY_KEY["kiseki"] not in run.remaining_targets(),
    )

    run.take_course({"dance": 10}, "speed course")
    ok &= check("a course debits tokens", run.tokens["dance"] == 90, str(run.tokens))
    ok &= check("courses are counted", run.courses_this_live == 1)
    ok &= check(
        "courses and songs both count as spending",
        run.spent_since_entry["dance"] == 10 and run.spent_since_entry["passion"] == 21,
        str(run.spent_since_entry),
    )
    ok &= check(
        "have now == entered minus spent",
        all(run.tokens[t] == run.entered[t] - run.spent_since_entry[t] for t in gd.TOKENS),
    )

    # Re-entering starts a fresh baseline without losing the purchase history.
    run.set_tokens({t: 50 for t in gd.TOKENS})
    ok &= check("re-entering resets the baseline", run.tokens["dance"] == 50, str(run.tokens))
    ok &= check("nothing counted as spent yet", not any(run.spent_since_entry.values()))
    ok &= check("purchases survive a re-entry", "kiseki" in run.bought)
    ok &= check(
        "lifetime spend still includes earlier purchases",
        run.spent_total["passion"] == 21 and run.spent_total["dance"] == 10,
        str(run.spent_total),
    )

    run.advance_live()
    ok &= check("advancing moves to Live 2", run.live == 2)
    ok &= check("course count resets per Live", run.courses_this_live == 0)
    ok &= check("purchases persist across Lives", "kiseki" in run.bought)

    # Tokens may legitimately go negative if you mis-enter; report, never clamp.
    run.set_tokens({t: 5 for t in gd.TOKENS})
    run.buy_song("seishun")  # Vo 32, Co 12
    ok &= check("overspend is visible, not clamped", run.tokens["vocal"] == -27, str(run.tokens))
    ok &= check("overspend is flagged", run.overspent(), "expected overspent() to be True")

    # Refunds put the tokens back.
    run.unbuy_song("seishun")
    ok &= check(
        "refunding a song restores the balance",
        run.tokens["vocal"] == 5 and "seishun" not in run.bought,
        str(run.tokens),
    )

    run.reset()
    ok &= check(
        "reset clears the run",
        run.live == 1 and not run.bought and not run.has_baseline and not run.ledger,
    )

    # 7. Spare-for-courses: what is left after covering every song worth
    #    buying. This is the number courses should be paid out of.
    spare_run = RunState()
    need = spare_run.guide_requirement()          # Live 1: 67/21/53/49/68
    spare_run.set_tokens({t: need[t] + 10 for t in gd.TOKENS})
    ok &= check(
        "spare is balance minus the guide requirement",
        spare_run.spare() == {t: 10 for t in gd.TOKENS},
        str(spare_run.spare()),
    )
    ok &= check("spare total sums the surplus", spare_run.spare_total() == 50)
    ok &= check("nothing is short when spare is positive", not any(spare_run.shortfall().values()))

    # Buying a song drops the requirement and the balance by the same amount,
    # so the spare must not move.
    before_spare = dict(spare_run.spare())
    spare_run.buy_song("kiseki")
    ok &= check(
        "buying a song the guide wanted leaves spare unchanged",
        spare_run.spare() == before_spare,
        f"{spare_run.spare()} vs {before_spare}",
    )

    # A course, by contrast, comes straight out of the spare.
    spare_run.take_course({"dance": 10})
    ok &= check(
        "a course comes out of the spare",
        spare_run.spare()["dance"] == 0 and spare_run.spare()["vocal"] == 10,
        str(spare_run.spare()),
    )
    ok &= check("spare total drops with it", spare_run.spare_total() == 40)

    # Overspending on courses shows as negative spare and as a shortfall.
    spare_run.take_course({"vocal": 25})
    ok &= check("overspending shows negative spare", spare_run.spare()["vocal"] == -15)
    ok &= check(
        "shortfall mirrors the negative spare",
        spare_run.shortfall()["vocal"] == 15 and spare_run.shortfall()["dance"] == 0,
        str(spare_run.shortfall()),
    )
    ok &= check(
        "a surplus elsewhere does not mask the shortfall",
        spare_run.spare_total() == 30,
        str(spare_run.spare_total()),
    )

    # 8. Song titles should be the ones an English account shows.
    ok &= check(
        "songs use Global English titles",
        gd.BY_KEY["kiseki"].name == "Believe in Miracles!"
        and gd.BY_KEY["runrun"].name == "Run n' Run!"
        and gd.BY_KEY["seishun"].name == "Here Comes Our Time"
        and gd.BY_KEY["takarabako"].name == "Precious Treasure Box",
        gd.BY_KEY["kiseki"].name,
    )
    ok &= check(
        "romaji titles are kept for cross-referencing",
        gd.BY_KEY["kiseki"].romaji == "Kiseki wo Shinjite!"
        and gd.BY_KEY["gothisway"].romaji is None,
    )
    ok &= check(
        "no song title is left in romaji",
        not [s for s in gd.SONGS if s.romaji and s.name == s.romaji],
    )

    # 9. Undo.
    run2 = RunState()
    run2.set_tokens({t: 100 for t in gd.TOKENS})
    run2.buy_song("zensoku")
    run2.undo()
    ok &= check(
        "undo restores tokens and purchases",
        not run2.bought and run2.tokens["dance"] == 100,
        str(run2.tokens),
    )

    # 8. A version-1 save must migrate without double-charging.
    legacy = {
        "live": 2,
        "tokens": {"dance": 40, "passion": 30, "vocal": 20, "visual": 10, "mental": 60},
        "bought": ["kiseki"],
        "courses": [{"live": 1, "cost": {"dance": 10}, "note": "old course"}],
        "finished": False,
    }
    migrated = RunState.from_dict(legacy)
    ok &= check(
        "old saves keep their balance, with Mental read as Composure",
        migrated.tokens == {"dance": 40, "passion": 30, "vocal": 20, "visual": 10, "composure": 60},
        str(migrated.tokens),
    )
    ok &= check("old saves keep their purchases", migrated.bought == ["kiseki"])
    ok &= check("old saves keep their courses", len(migrated.courses) == 1)
    ok &= check(
        "round-trips through JSON",
        RunState.from_dict(json.loads(json.dumps(migrated.to_dict()))).tokens == migrated.tokens,
    )

    print("\nall good" if ok else "\nfailures above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
