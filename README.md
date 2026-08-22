# Uma Musume Team Trials builder

A local web app that keeps a database of every uma card, tracks which ones you
own, and works out the best possible 15-uma Team Trials roster from them.

## Quick start

```bash
python ingest.py                       # build uma.db (needs internet)
python -m streamlit run app.py         # open the app
```

On this machine Python is not on PATH — use the full interpreter path:

```bash
"C:/Users/tonyz/AppData/Local/Python/bin/python.exe" -m streamlit run app.py
```

## What's in the database

`uma.db` is a SQLite file built by `ingest.py` from two public sources that join
cleanly on the card id (tracentrial's `form_id` is GameTora's `card_id`):

| source | what it contributes |
| --- | --- |
| **tracentrial.org** | Team Trials tier (S/A/B/C) and the profile write-up shown when you click an uma on the tierlist, plus the skill catalogue |
| **gametora.com** | per-outfit distance/surface/running-style aptitudes, base and five-star stat caps, growth bonuses, Global release dates |

As of the last run: **263 cards** (99 released on Global, 96 of them tier-rated),
**843 skills**, **4,400 card-skill links**.

Two useful facts fell out of the data. The tierlist's `form_id` is exactly
GameTora's `card_id`, so no name matching is needed. And every unrated card is
one that has not reached Global yet — so "unrated" means "not out here", not
"bad". The app filters to Global cards by default because of that.

Tables split into two groups:

* **reference** — `uma_cards`, `skills`, `card_skills`, `ingest_log`. Dropped
  and rebuilt on every ingest.
* **yours** — `roster`, `roster_aptitude_override`, `saved_team`. Never touched
  by an ingest, so refreshing the card data is safe.

## How the optimizer works

A Team Trials roster is 15 umas: three in each of Sprint, Mile, Medium, Long and
Dirt. Each uma runs one slot, and you pick her running style. Three umas running
the same style in one category fight each other for position, so the styles
should differ.

That is an assignment problem, and it is solved exactly — not greedily — as a
min-cost flow:

```
source -> uma                capacity 1        each uma used at most once
uma    -> (category, style)  capacity 1        cost = -score
(category, style) -> category  3 parallel arcs, costs 0 / P / 2P
category -> sink             capacity 3        three umas per category
```

The parallel arcs are the trick: the first uma of a given style in a category is
free, the second costs the style penalty `P`, the third costs `2P`. Slide `P` to
0 and style is ignored; slide it up and three distinct styles per category become
effectively mandatory. `test_optimizer.py` checks the result against exhaustive
search on small instances at several penalties.

### Scoring

Each candidate is scored for each (category, style) pair:

```
score = w_apt   * 100 * (surface_mult * distance_mult * style_mult)
      + w_tier  * tier_points          S=100 A=75 B=50 C=25, unrated=40
      + w_stats * stat_points          five-star caps + growth, distance-weighted
```

All three weights and the style penalty are sliders in the sidebar. Aptitude fit
dominates by design: an uma who cannot run the race is useless no matter how the
tierlist rates her. Grade multipliers live at the top of `optimizer.py` so one
edit fixes every score if the game rebalances them.

### Assumptions worth knowing

* **Dirt distances.** Dirt is a surface, not a distance, and its races rotate.
  The app averages the aptitudes for whichever distances you select in the
  sidebar (mile + medium by default).
* **Stat weights per distance** (more stamina for Long, more power for Sprint)
  are hand-tuned heuristics, not measured values. They carry a small weight by
  default, so they mostly break ties.
* **Card aptitudes are base values.** If you have raised one through
  inheritance, override it on the *My umas* tab — the optimizer uses your grade.
* Scores are comparative, not a prediction of race points.

## Files

| file | purpose |
| --- | --- |
| `ingest.py` | fetch both sources, rebuild the reference tables |
| `db.py` | schema and connection |
| `roster.py` | reading cards, tracking what you own, aptitude overrides |
| `optimizer.py` | scoring model and the min-cost-flow solver |
| `app.py` | Streamlit UI: Optimize / My umas / Tierlist tabs |
| `test_optimizer.py` | solver checks, including agreement with brute force |

## Refreshing

The sidebar has a refresh button, or run `python ingest.py` again. GameTora
versions its data files by content hash, so `ingest.py` reads the manifest first
and always pulls the current file rather than a pinned URL. Your roster survives
the rebuild.
