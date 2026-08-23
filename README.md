# Uma Musume companion

A local web app for one account: a database of every uma card, a Team Trials
team optimizer, and a Live-by-Live tracker for Our Grand Concert runs.

## Quick start

```bash
python ingest.py                       # card + tierlist data
python guide_ingest.py                 # the tracentrial guide text
python -m streamlit run app.py
```

On this machine Python is not on PATH — use the full interpreter path:

```bash
"C:/Users/tonyz/AppData/Local/Python/bin/python.exe" -m streamlit run app.py
```

## Deploying to Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **Create app** → **Deploy a public app from GitHub**.
3. Repository `tonyzeng262/uma-musume-companion`, branch `main`,
   main file path `app.py`.
4. Deploy. The first load takes an extra 10–20 seconds while it builds the
   card database, then it behaves like the local copy.

`uma.db` is not committed — it is derived data, and a binary blob in git goes
stale silently. `bootstrap.py` rebuilds it from tracentrial.org and gametora.com
whenever the reference tables are missing, which is exactly what a fresh
container looks like. Reference portraits download on demand from the
*My umas* tab.

### The one real limitation

Streamlit Community Cloud gives every app a **throwaway filesystem**. It is
wiped when the app sleeps (after a few days idle) and on every redeploy. Card
data rebuilds itself, but **your roster and any Grand Live run in progress do
not survive** — they live in the same SQLite file. The app shows a warning in
the sidebar when it detects it is running hosted.

If that becomes annoying, the fix is to move the four user tables (`roster`,
`roster_aptitude_override`, `grand_live_run`, `grand_live_history`) onto a
hosted database — Turso/libSQL works well and `db.connect()` is the only place
that would need to change.

## Layout

Every tab is a dashboard that fits a 1280x800 window with no page scrolling —
measured, not eyeballed. Long content lives in fixed-height panes that scroll
internally, or in popovers, so nothing you need mid-run is below the fold.

The run tracker is a status strip (Live stepper, songs, spare, courses,
End Live / Undo / More) over three columns: **your tokens**, **the songs**,
**your courses**. Tokens and course costs are entered as one line of five
numbers in `Da Pa Vo Vi Co` order — `90 45 70 65 85` — rather than five
spinners. The team board puts one race category per column, so all fifteen
umas are visible at once.

## Tabs

| tab | what it does |
| --- | --- |
| **Optimize** | Best 15-uma Team Trials roster from the umas you own |
| **My umas** | Pick what you own — by name, in bulk, or from screenshots |
| **Tierlist** | Every card's tier, write-up, aptitudes and skills |
| **Grand Live run** | Live-by-Live coin and song tracker for a run in progress |
| **Song values** | All 21 lesson songs by net value and real token cost |
| **Guide** | The imported tracentrial guide sections |

## Where the data comes from

`uma.db` is SQLite, built by two importers from sources that join cleanly.

| source | what it gives |
| --- | --- |
| **tracentrial.org** (API) | Team Trials tier + profile write-ups, skill catalogue |
| **tracentrial.org** (bundle) | song net values, Live-by-Live strategy, guide prose |
| **gametora.com** | aptitudes, stat caps, growth, release dates, portraits |

Two facts make the joins trivial: tracentrial's `form_id` is GameTora's
`card_id`, and every card the tierlist leaves unrated is simply one not yet
released on Global.

Tables split into **reference** (rebuilt on every import) and **yours**
(`roster`, `roster_aptitude_override`, `grand_live_run`, `grand_live_history`,
`saved_team` — never touched by an import), so refreshing is always safe.

The guide pages have no API — the text is compiled into the site's React
bundle. `guide_ingest.py` reads the current bundle (found via index.html, since
the filename is content-hashed) and recovers the data **by shape, not by
minified variable name**, so it survives a rebuild of the site. Two of the
thirteen sections are support-card tables rather than prose and are skipped.

## The Team Trials optimizer

Fifteen umas, three in each of Sprint / Mile / Medium / Long / Dirt, each given
a running style. Three umas on the same style in one category fight for
position, so the styles should differ. Solved exactly as a min-cost flow:

```
source -> uma                capacity 1        each uma used at most once
uma    -> (category, style)  capacity 1        cost = -score
(category, style) -> category  3 parallel arcs, costs 0 / P / 2P
category -> sink             capacity 3        three umas per category
```

The parallel arcs are the trick: the first uma of a style in a category is
free, the second costs `P`, the third `2P`. Slide `P` to 0 and style is
ignored; slide it up and distinct styles become effectively mandatory.
`test_optimizer.py` checks the answer against exhaustive search.

Scoring, all weights adjustable in the sidebar:

```
score = w_apt   * 100 * (surface_mult * distance_mult * style_mult)
      + w_tier  * tier_points          S=100 A=75 B=50 C=25, unrated=40
      + w_stats * stat_points          caps + growth, weighted for the distance
```

## The Grand Live run tracker

Tracks one run: which Live you are in, the tokens you actually hold, the songs
you have bought and the courses you have taken.

### Your balance is a ledger, not a number

Nothing is ever edited in place. The run keeps an ordered ledger:

```
set     "the game is showing me these numbers right now"
song    bought a song, at its known token cost
course  took a Live Technique course, at the cost you typed in
```

Your balance is *derived* — take the most recent `set` as a fixed baseline,
then subtract everything spent after it. That gives three figures that are
each useful and always agree:

| | |
| --- | --- |
| **Entered** | the static figure you last typed in |
| **Spent** | exactly what has gone out since then |
| **Balance** | entered minus spent |

Overwriting a balance would destroy *spent*, which is the number you actually
want when deciding whether you can still afford what the guide is pushing you
toward. Re-entering your tokens just opens a fresh baseline, so correcting
mid-run costs nothing and the purchase history survives. The whole ledger is
visible in the app as an audit trail.

### Spare for courses

The headline figure is not your balance but your **spare**: what is left after
covering every song still worth buying.

```
spare = balance - guide requirement
```

That is the number courses should be paid out of, because the guide's rule is
never to let a course purchase cost you a high-scoring song. Anything above
zero is safe to spend; anything below is already borrowed from a song, and the
app says so instead of letting it hide.

It behaves the way you would want: buying a song the guide wanted drops the
balance *and* the requirement by the same amount, so the spare does not move.
Taking a course comes straight out of it. The total only counts positive
tokens — a pile of spare Dance cannot pay for a course that wants Vocal.

Songs never leave the pool until bought, so an unbought Live 1 song is still
cluttering Live 4 — the "pool contamination" the guide warns about.

Ending a Live advances the tracker; **Archive and restart** files the run away
in `grand_live_history`; **Restart** wipes it. Every action is undoable.

Token counts are never clamped: if one goes negative the app says so, because
that means a purchase was double-logged or the baseline was stale.

### Naming follows the Global client

Both data sources are Japan-first, so a few things needed translating for an
English account.

**The fifth token.** GameTora calls the tokens Dance, Passion, Vocal, Visual
and *Mental*, straight from メンタル. Global localised that last one to
**Composure**. The app uses the game's wording — Da / Pa / Vo / Vi / **Co** —
with the in-game hexagon colours (blue, red, pink, yellow, violet). Saves
written before this still load; `mental` is read as `composure`.

**Song titles.** GameTora, and tracentrial's values page, list several songs
under Japanese-derived names. The app shows the Global English titles (*Believe
in Miracles!*, *Run n' Run!*, *Here Comes Our Time*, *Zero Is Where the Center
Stands!* …) and keeps each song's `romaji` alongside, shown as a small "JP:"
note, so a row can still be matched against either source. Titles come from
tracentrial's Section 02 listing and Game8's Grand Concert song ranking.

One caveat: *Getaway! Fallin' Love* was reconstructed from a source that
truncated it mid-word. If the game shows something different, that one entry is
the likeliest to be wrong.

### The song catalogue is cross-validated, not trusted

`grandlive_data.py` holds all 21 lesson songs with per-token costs (Dance,
Passion, Vocal, Visual, Mental). It is curated by hand — GameTora renders that
table as prose in a hand-written article, so a scraper would be far more
fragile than the data is volatile. `test_grandlive.py` validates it against two
figures published independently of each other:

* column totals must equal GameTora's stated "total cost of all songs"
  (252 / 201 / 150 / 275 / 196);
* per Live, the token sum of the positive-value songs must equal the coin
  requirement tracentrial quotes (Live 1 = 67/21/53/49/68, Live 2 =
  63/21/0/84/0, Live 4 = 80/54/34/100/44).

Both hold exactly. That also pins the mapping between the two sites' different
names for the same songs (tracentrial's "Precious Treasure Box" is GameTora's
"Daisuki no Takarabako").

**One correction that fell out of this:** tracentrial lists the two
Friendship +10% songs at 78 tokens. They cost **68** (42+26 and 26+42). Its own
per-Live requirement figures confirm 68, so the 78 is a typo on its values
page. The app uses 68.

## Screenshot import

Upload screenshots of your roster and the app matches each tile against
GameTora's reference portraits — entirely offline, nothing is uploaded.

Each tile gets two signatures: a **difference hash** of the greyscale image
(structure — hair silhouette, pose, light and dark regions) and a small
**colour grid**. Structure is weighted higher, because colour is thrown off by
whatever background the game composites behind the portrait. Both are computed
on a centre crop, since tiles put level text, stars and badges around the edge.

`test_portraits.py` degrades every reference portrait the way a game tile does
— rescaled, tinted background, frame, corner badge, JPEG noise — and measures
recovery: **100% top-1 across all 99 Global cards**, correct match at distance
0.039 versus 0.367 for an unrelated card.

That is a synthetic upper bound, not a promise. Real screenshots crop the
artwork differently, so the grid is adjustable (rows, columns, edge crop, tile
inset) and **every match is shown for approval with a confidence score and a
dropdown of the top five alternatives before anything is written**.

## Assumptions worth knowing

* **Dirt** is a surface, not a distance, and its Team Trials races rotate. The
  app averages the aptitudes for whichever distances you pick in the sidebar
  (mile + medium by default). It is still a required category, so it is always
  filled.
* **Per-distance stat weights** (more stamina for Long, more power for Sprint)
  are hand-tuned heuristics, not measured values. Small weight by default, so
  they mostly break ties.
* **Card aptitudes are base values.** Raised one through inheritance? Override
  it on the *My umas* tab.
* Team scores are comparative, not a prediction of race points.

## Files

| file | purpose |
| --- | --- |
| `ingest.py` / `guide_ingest.py` | the two importers |
| `db.py` | schema and connection |
| `roster.py` | cards, ownership, aptitude overrides |
| `optimizer.py` | scoring model and min-cost-flow solver |
| `grandlive_data.py` | the verified song catalogue |
| `grandlive.py` | run state, persistence, arithmetic |
| `portraits.py` | offline portrait hashing and matching |
| `app.py` + `views_*.py` | the Streamlit UI |
| `test_*.py` | solver, catalogue and matcher checks |

Run all checks:

```bash
python test_optimizer.py && python test_grandlive.py && python test_portraits.py
```
