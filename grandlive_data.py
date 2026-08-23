"""The Our Grand Concert (Grand Live) song catalogue.

Costs are in performance tokens, always in the order Dance, Passion, Vocal,
Visual, Composure -- the column order GameTora uses on its scenario page
(da/pa/vo/vi/me icons, confirmed from the page DOM).

This table is curated rather than scraped: GameTora renders it as prose inside
a hand-written article, so a scraper would be far more fragile than the data is
volatile. It is not taken on trust either -- `test_grandlive.py` checks it two
independent ways:

  * per-token column totals must equal GameTora's published "total cost of all
    songs" row (252 / 201 / 150 / 275 / 196);
  * for each Live, the per-token sum of the positive-value songs must equal the
    coin requirement tracentrial.org quotes in its strategy guide
    (Live 1 = 67/21/53/49/68, Live 2 = 63/21/0/84/0, Live 4 = 80/54/34/100/44).

Both sources agreeing to the token on those figures is strong evidence the
catalogue is right.

Song titles are the **Global English** ones, since that is what the game shows
on an English account. GameTora and tracentrial's values page both use
Japanese-derived names for several songs, so each entry also keeps its `romaji`
title for cross-referencing those sources. The English titles come from
tracentrial's own Section 02 listing and from Game8's Grand Concert song
ranking; "Getaway! Fallin' Love" is the one reconstructed from a truncated
source, so check it against the game if it looks wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

# The five performance tokens, in the column order GameTora uses on its
# scenario page (da/pa/vo/vi/me icons, confirmed from the page DOM).
#
# Note the fifth: GameTora calls it "Mental", straight from the Japanese
# メンタル, but Global localised it to **Composure**. The game's own wording
# wins here, so the key, label and colour all say Composure.
TOKENS = ("dance", "passion", "vocal", "visual", "composure")
TOKEN_LABELS = {
    "dance": "Dance",
    "passion": "Passion",
    "vocal": "Vocal",
    "visual": "Visual",
    "composure": "Composure",
}
TOKEN_SHORT = {
    "dance": "Da",
    "passion": "Pa",
    "vocal": "Vo",
    "visual": "Vi",
    "composure": "Co",
}
# The in-game hexagon colours.
TOKEN_COLOR = {
    "dance": "#2f7fd4",      # blue
    "passion": "#dc4340",    # red
    "vocal": "#e35f9c",      # pink
    "visual": "#d9a316",     # yellow
    "composure": "#8258cc",  # violet
}

# GameTora's published grand total for practising every lesson song in one run.
PUBLISHED_TOTAL = (252, 201, 150, 275, 196)

# Coin requirements tracentrial quotes per Live, for the songs worth buying.
# Live 3 is not quoted in the guide (its advice is "the pool has not updated").
TRACENTRIAL_REQUIREMENTS = {
    1: (67, 21, 53, 49, 68),
    2: (63, 21, 0, 84, 0),
    4: (80, 54, 34, 100, 44),
}


@dataclass(frozen=True)
class Song:
    """One song lesson.

    `live` is the Live whose pool it *joins*; unbought songs stay in the pool
    for every later Live, which is the "pool contamination" the guide warns
    about. `net_value` is tracentrial's estimated attribute-point value of
    buying it on time; None means the guide does not rate it.
    """

    key: str
    name: str             # the Global English title, as the game shows it
    romaji: str | None    # GameTora's Japanese-derived name, when it differs
    live: int
    cost: tuple[int, int, int, int, int]
    effect: str
    net_value: float | None
    special: bool = False

    @property
    def total_cost(self) -> int:
        return sum(self.cost)

    @property
    def display_name(self) -> str:
        return self.name

    def cost_map(self) -> dict[str, int]:
        return dict(zip(TOKENS, self.cost))


SONGS: tuple[Song, ...] = (
    # --- Live 1: available from the start ---------------------------------
    Song("kiseki", "Believe in Miracles!", "Kiseki wo Shinjite!", 1, (0, 21, 0, 0, 21),
         "Training Wit Gain +1, Speciality Priority Up +5", 33.0),
    Song("tachiichi", "Zero Is Where the Center Stands!",
         "Tachiichi zero-ban! Juni wa Ichiban!", 1, (21, 0, 0, 21, 0),
         "Training Speed Gain +1, Support Chain Event Frequency +1", 29.0),
    Song("nigekiri", "Getaway! Fallin' Love", "Nigekiri! Fallin' Love", 1, (21, 0, 0, 21, 0),
         "Training Guts Gain +1, Support Chain Event Frequency +1", -5.0),
    Song("gothisway", "Go This Way", None, 1, (0, 0, 21, 0, 21),
         "Training Power Gain +1, Support Chain Event Frequency +1", 9.0),
    Song("ringring", "Ring Ring Diary", None, 1, (0, 21, 0, 21, 0),
         "Training Stamina Gain +1, Support Chain Event Frequency +1", -5.0),
    Song("seishun", "Here Comes Our Time", "Seishun ga Matteru", 1, (0, 0, 32, 0, 12),
         "Power +22, Friendship Bonus +5%", 67.0),
    Song("runrun", "Run n' Run!", "RUN×RUN!", 1, (14, 0, 0, 16, 14),
         "Skill Points +22, Friendship Bonus +5%", 54.0),
    Song("zensoku", "Full Speed Ahead! Umadol Power☆", "Zensoku! Zenshin! Umadol Power☆",
         1, (32, 0, 0, 12, 0),
         "Speed +22, Friendship Bonus +5%", 65.0),

    # --- Live 2: added after the 1st Promotional Live ----------------------
    Song("yumewokakeru", "Run for Our Dream!", "Yume wo Kakeru!", 2, (0, 21, 0, 21, 0),
         "Training Skill Point Bonus +2, Speciality Priority Up +5", 34.0),
    Song("anone", "Hey, Guess What!", "A・NO・NE", 2, (42, 0, 0, 21, 0),
         "Training Guts Gain +2, Speciality Priority Up +5", 0.5),
    Song("bluebird", "Our Blue Bird Days", "Bokura no Bluebird Days", 2, (21, 0, 0, 42, 0),
         "Training Speed Gain +2, Speciality Priority Up +5", 22.5),

    # --- Live 3: added after the 2nd Promotional Live ----------------------
    Song("growup", "Grow Up and Shine!", "Grow Up, Shine!", 3, (21, 0, 21, 0, 21),
         "Training Skill Point Bonus +3, Support Chain Event Frequency +1", 0.0),
    Song("komorebi", "Sunbeam Cheer", "Komorebi no Yell", 3, (0, 42, 0, 0, 21),
         "Training Wit Gain +2, Support Chain Event Frequency +1", -1.5),
    Song("pyoitto", "Hoppity Sunny Days♪", "Pyoitto ♪ Hallelujah!", 3, (0, 42, 21, 0, 0),
         "Training Stamina Gain +2, Speciality Priority Up +5", -6.0),
    Song("nanairo", "Seven Colors Scenery", "Nanairo no Keshiki", 3, (0, 0, 21, 0, 42),
         "Training Power Gain +2, Speciality Priority Up +5", 8.0),

    # --- Live 4: added after the 3rd Promotional Live ----------------------
    Song("yumezora", "Dream Sky", "Yumezora", 4, (0, 22, 0, 0, 22),
         "Wit +22, Friendship Bonus +5%", 29.0),
    Song("presentmarch", "Present March♪", "PRESENT MARCH♪", 4, (0, 0, 22, 0, 22),
         "Power +22, Friendship Bonus +5%", 29.0),
    Song("takarabako", "Precious Treasure Box", "Daisuki no Takarabako", 4, (42, 0, 0, 26, 0),
         "Speed +26, Friendship Bonus +10%", 36.0),
    Song("sekai", "The World's at Our Whim", "Sekai wa Bokura no Iinari Sa", 4, (0, 32, 12, 0, 0),
         "Stamina +22, Friendship Bonus +5%", 29.0),
    Song("harusora", "Sky-Blue Spring", "Harusora BLUE", 4, (12, 0, 0, 32, 0),
         "Guts +22, Friendship Bonus +5%", 29.0),
    Song("fanfare", "Fanfare for the Future!", "Fanfare for Future!", 4, (26, 0, 0, 42, 0),
         "Guts +26, Friendship Bonus +10%", 36.0),

    # --- Specials: granted, never bought from a lesson ---------------------
    Song("makedebut", "Make Debut!", None, 1, (0, 0, 0, 0, 0),
         "All Tokens +10, Speciality Priority Up +5", None, special=True),
    Song("girlslegend", "GIRLS' LEGEND U", None, 5, (0, 0, 0, 0, 0),
         "All Stats +10, Friendship Bonus +10%", None, special=True),
)

BY_KEY = {s.key: s for s in SONGS}

# Typical Live Technique ("course") cost per block, from the tracentrial guide.
COURSE_COST_HINT = {1: 10, 2: 16, 3: 16, 4: 24, 5: 24}

LIVE_LABELS = {
    1: "Live 1 - Junior year to the 1st Promotional Live",
    2: "Live 2 - to the 2nd Promotional Live",
    3: "Live 3 - to the 3rd Promotional Live",
    4: "Live 4 - to the 4th Promotional Live",
    5: "Live 5 - Senior year, the Grand Live itself",
}

# tracentrial notes that GIRLS' LEGEND U unlocks at 18 songs learned, and that
# a Grand Success needs 19 or more.
GRAND_SUCCESS_SONGS = 19
GIRLS_LEGEND_UNLOCK = 18


def songs_for_live(live: int, include_special: bool = False) -> list[Song]:
    """Songs that join the pool at `live`."""
    return [s for s in SONGS if s.live == live and (include_special or not s.special)]


def pool_at(live: int, bought: set[str]) -> list[Song]:
    """Every buyable song available during `live` that has not been bought.

    Songs never leave the pool once introduced, so an unbought Live 1 song is
    still cluttering the pool in Live 4.
    """
    return [
        s for s in SONGS
        if not s.special and s.live <= live and s.key not in bought
    ]


def requirement(songs: list[Song]) -> tuple[int, ...]:
    """Per-token sum of a set of songs."""
    return tuple(sum(s.cost[i] for s in songs) for i in range(len(TOKENS)))


def worth_buying(songs: list[Song], threshold: float = 0.0) -> list[Song]:
    return [s for s in songs if (s.net_value or 0) > threshold]
