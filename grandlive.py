"""Live-by-Live run tracker for the Our Grand Concert scenario.

Holds the state of one training run: which Live you are in, how many
performance tokens you actually have, which songs you have bought, and what
you have spent on Live Technique courses. Everything is persisted so the run
survives closing the browser.

Two different "how much do I need" numbers matter and are kept apart:

  * the **guide requirement** -- what the songs still worth buying in the
    current pool would cost you in total. It shrinks as you buy them.
  * your **actual tokens** -- what you have in the run right now. Buying a
    song or taking a course debits this.

The gap between the two is the shortfall, which is the number the strategy
guide is really about.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

import grandlive_data as gd

MAX_UNDO = 60
LAST_LIVE = 5


@dataclass
class CourseEntry:
    live: int
    cost: dict[str, int]
    note: str = ""


@dataclass
class RunState:
    live: int = 1
    tokens: dict[str, int] = field(default_factory=lambda: {t: 0 for t in gd.TOKENS})
    bought: list[str] = field(default_factory=list)
    courses: list[CourseEntry] = field(default_factory=list)
    finished: bool = False
    _undo: list[str] = field(default_factory=list, repr=False)

    # --- history ---------------------------------------------------------

    def _snapshot(self) -> None:
        self._undo.append(json.dumps(self.to_dict()))
        del self._undo[:-MAX_UNDO]

    def undo(self) -> bool:
        if not self._undo:
            return False
        prior = json.loads(self._undo.pop())
        keep = self._undo
        self.__dict__.update(RunState.from_dict(prior).__dict__)
        self._undo = keep
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    # --- actions ---------------------------------------------------------

    def set_tokens(self, tokens: dict[str, int], record: bool = True) -> None:
        """Overwrite your token counts with what the game is showing you."""
        if record:
            self._snapshot()
        self.tokens = {t: int(tokens.get(t, self.tokens.get(t, 0))) for t in gd.TOKENS}

    def buy_song(self, key: str) -> None:
        song = gd.BY_KEY[key]
        if key in self.bought:
            return
        self._snapshot()
        self.bought.append(key)
        for token, cost in song.cost_map().items():
            self.tokens[token] -= cost

    def unbuy_song(self, key: str) -> None:
        """Undo a purchase you logged by mistake, refunding the tokens."""
        if key not in self.bought:
            return
        self._snapshot()
        self.bought.remove(key)
        for token, cost in gd.BY_KEY[key].cost_map().items():
            self.tokens[token] += cost

    def take_course(self, cost: dict[str, int], note: str = "") -> None:
        self._snapshot()
        spend = {t: int(cost.get(t, 0)) for t in gd.TOKENS}
        self.courses.append(CourseEntry(self.live, spend, note))
        for token, amount in spend.items():
            self.tokens[token] -= amount

    def advance_live(self) -> None:
        self._snapshot()
        if self.live >= LAST_LIVE:
            self.finished = True
        else:
            self.live += 1

    def reset(self) -> None:
        self._snapshot()
        self.live = 1
        self.tokens = {t: 0 for t in gd.TOKENS}
        self.bought = []
        self.courses = []
        self.finished = False

    # --- derived numbers --------------------------------------------------

    @property
    def courses_this_live(self) -> int:
        return sum(1 for c in self.courses if c.live == self.live)

    @property
    def spent_on_courses(self) -> dict[str, int]:
        out = {t: 0 for t in gd.TOKENS}
        for c in self.courses:
            for token, amount in c.cost.items():
                out[token] += amount
        return out

    def pool(self) -> list[gd.Song]:
        """Every buyable song still available to you right now."""
        return gd.pool_at(self.live, set(self.bought))

    def remaining_targets(self, threshold: float = 0.0) -> list[gd.Song]:
        """Pool songs the guide still rates as worth buying."""
        return gd.worth_buying(self.pool(), threshold)

    def guide_requirement(self, threshold: float = 0.0) -> dict[str, int]:
        """What the songs still worth buying would cost, per token."""
        return dict(zip(gd.TOKENS, gd.requirement(self.remaining_targets(threshold))))

    def new_this_live_requirement(self, threshold: float = 0.0) -> dict[str, int]:
        """The guide's headline number: songs introduced in this Live only."""
        fresh = [
            s for s in gd.songs_for_live(self.live)
            if s.key not in self.bought and (s.net_value or 0) > threshold
        ]
        return dict(zip(gd.TOKENS, gd.requirement(fresh)))

    def shortfall(self, threshold: float = 0.0) -> dict[str, int]:
        """Tokens you are still missing, per type. Zero means you can afford it."""
        need = self.guide_requirement(threshold)
        return {t: max(0, need[t] - self.tokens.get(t, 0)) for t in gd.TOKENS}

    def can_afford(self, key: str) -> bool:
        return all(self.tokens.get(t, 0) >= c for t, c in gd.BY_KEY[key].cost_map().items())

    def overspent(self) -> bool:
        return any(v < 0 for v in self.tokens.values())

    @property
    def songs_learned(self) -> int:
        """Bought songs plus the two you are granted automatically."""
        return len(self.bought) + sum(1 for s in gd.SONGS if s.special)

    @property
    def on_track_for_grand_success(self) -> bool:
        return self.songs_learned >= gd.GRAND_SUCCESS_SONGS

    # --- persistence ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "live": self.live,
            "tokens": self.tokens,
            "bought": list(self.bought),
            "courses": [{"live": c.live, "cost": c.cost, "note": c.note} for c in self.courses],
            "finished": self.finished,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunState":
        state = cls()
        state.live = int(data.get("live", 1))
        stored = data.get("tokens") or {}
        state.tokens = {t: int(stored.get(t, 0)) for t in gd.TOKENS}
        state.bought = [k for k in data.get("bought", []) if k in gd.BY_KEY]
        state.courses = [
            CourseEntry(
                int(c.get("live", 1)),
                {t: int((c.get("cost") or {}).get(t, 0)) for t in gd.TOKENS},
                c.get("note", ""),
            )
            for c in data.get("courses", [])
        ]
        state.finished = bool(data.get("finished"))
        return state


SCHEMA = """
CREATE TABLE IF NOT EXISTS grand_live_run (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL,
    undo    TEXT NOT NULL DEFAULT '[]',
    saved_at TEXT DEFAULT (datetime('now'))
);

-- Runs you finished, kept so you can look back at what a good run cost.
CREATE TABLE IF NOT EXISTS grand_live_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ended_at   TEXT DEFAULT (datetime('now')),
    outcome    TEXT,
    payload    TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def load(conn: sqlite3.Connection) -> RunState:
    ensure_schema(conn)
    row = conn.execute("SELECT payload, undo FROM grand_live_run WHERE id = 1").fetchone()
    if not row:
        return RunState()
    state = RunState.from_dict(json.loads(row["payload"]))
    try:
        state._undo = json.loads(row["undo"])[-MAX_UNDO:]
    except (TypeError, ValueError):
        state._undo = []
    return state


def save(conn: sqlite3.Connection, state: RunState) -> None:
    ensure_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO grand_live_run (id, payload, undo, saved_at)"
            " VALUES (1, ?, ?, datetime('now'))"
            " ON CONFLICT(id) DO UPDATE SET payload = excluded.payload,"
            " undo = excluded.undo, saved_at = excluded.saved_at",
            (json.dumps(state.to_dict()), json.dumps(state._undo[-MAX_UNDO:])),
        )


def archive(conn: sqlite3.Connection, state: RunState, outcome: str) -> None:
    """File the finished run away and clear the tracker."""
    ensure_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO grand_live_history (outcome, payload) VALUES (?, ?)",
            (outcome, json.dumps(state.to_dict())),
        )


def history(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    ensure_schema(conn)
    return list(
        conn.execute(
            "SELECT * FROM grand_live_history ORDER BY ended_at DESC LIMIT ?", (limit,)
        )
    )
