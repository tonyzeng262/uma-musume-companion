"""Live-by-Live run tracker for the Our Grand Concert scenario.

Holds the state of one training run: which Live you are in, how many
performance tokens you actually have, which songs you have bought, and what
you have spent on Live Technique courses. Everything is persisted so the run
survives closing the browser.

## Why this is a ledger

Your token balance is never stored as a number that gets edited in place.
Instead the run keeps an ordered ledger of entries:

    set    -- "the game is showing me these numbers right now"
    song   -- bought a song, at its known token cost
    course -- took a Live Technique course, at the cost you typed in

Your current balance is *derived*: take the most recent `set` entry as the
baseline, then subtract everything spent after it. That gives three numbers
that are all separately useful and always agree with each other:

    entered   -- the static figure you last typed in
    spent     -- exactly what has gone out since then
    have now  -- entered minus spent

Editing a balance in place would lose "spent", which is the number you
actually want when deciding whether you can still afford the songs the guide
wants. Re-entering your tokens simply starts a fresh baseline, so a mid-run
correction costs you nothing.

Two different "how much do I need" numbers matter and are also kept apart:

  * the **guide requirement** -- what the songs still worth buying in the
    current pool would cost. It shrinks as you buy them.
  * your **actual tokens** -- the derived balance above.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

import grandlive_data as gd

MAX_UNDO = 60
LAST_LIVE = 5

SET = "set"
SONG = "song"
COURSE = "course"
SPEND_KINDS = (SONG, COURSE)


def _zero() -> dict[str, int]:
    return {t: 0 for t in gd.TOKENS}


@dataclass
class Entry:
    """One line of the ledger.

    For `set`, `amounts` is an absolute balance. For `song` and `course` it is
    what that action cost.
    """

    kind: str
    live: int
    amounts: dict[str, int] = field(default_factory=_zero)
    key: str = ""      # song key, for kind == SONG
    label: str = ""    # free-text note, for kind == COURSE

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "live": self.live,
            "amounts": self.amounts,
            "key": self.key,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        amounts = data.get("amounts") or {}
        return cls(
            kind=str(data.get("kind", SET)),
            live=int(data.get("live", 1)),
            amounts={t: int(amounts.get(t, 0)) for t in gd.TOKENS},
            key=str(data.get("key", "")),
            label=str(data.get("label", "")),
        )


@dataclass
class RunState:
    live: int = 1
    ledger: list[Entry] = field(default_factory=list)
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
        restored = RunState.from_dict(prior)
        self.live, self.ledger, self.finished = restored.live, restored.ledger, restored.finished
        self._undo = keep
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    # --- actions ---------------------------------------------------------

    def set_tokens(self, tokens: dict[str, int]) -> None:
        """Record what the game is showing you. Starts a fresh baseline."""
        self._snapshot()
        self.ledger.append(
            Entry(SET, self.live, {t: int(tokens.get(t, 0)) for t in gd.TOKENS})
        )

    def buy_song(self, key: str) -> None:
        if key in self.bought:
            return
        self._snapshot()
        song = gd.BY_KEY[key]
        self.ledger.append(Entry(SONG, self.live, song.cost_map(), key=key))

    def unbuy_song(self, key: str) -> None:
        """Drop a purchase logged by mistake. The tokens come back with it."""
        for i, entry in enumerate(self.ledger):
            if entry.kind == SONG and entry.key == key:
                self._snapshot()
                del self.ledger[i]
                return

    def take_course(self, cost: dict[str, int], note: str = "") -> None:
        self._snapshot()
        self.ledger.append(
            Entry(COURSE, self.live, {t: int(cost.get(t, 0)) for t in gd.TOKENS}, label=note)
        )

    def drop_course(self, index: int) -> None:
        """Remove the nth course entry, refunding it."""
        courses = [i for i, e in enumerate(self.ledger) if e.kind == COURSE]
        if 0 <= index < len(courses):
            self._snapshot()
            del self.ledger[courses[index]]

    def advance_live(self) -> None:
        self._snapshot()
        if self.live >= LAST_LIVE:
            self.finished = True
        else:
            self.live += 1

    def goto_live(self, live: int) -> None:
        """Jump straight to a Live -- for fixing a mis-click, mostly."""
        live = max(1, min(LAST_LIVE, int(live)))
        if live == self.live:
            return
        self._snapshot()
        self.live = live
        self.finished = False

    def reset(self) -> None:
        self._snapshot()
        self.live = 1
        self.ledger = []
        self.finished = False

    # --- the derived balance ---------------------------------------------

    @property
    def _baseline_index(self) -> int:
        """Position of the most recent balance entry, or -1 if never set."""
        for i in range(len(self.ledger) - 1, -1, -1):
            if self.ledger[i].kind == SET:
                return i
        return -1

    @property
    def has_baseline(self) -> bool:
        return self._baseline_index >= 0

    @property
    def entered(self) -> dict[str, int]:
        """The static figure you last typed in."""
        i = self._baseline_index
        return dict(self.ledger[i].amounts) if i >= 0 else _zero()

    @property
    def spent_since_entry(self) -> dict[str, int]:
        """Everything spent after that figure was entered."""
        out = _zero()
        for entry in self.ledger[self._baseline_index + 1 :]:
            if entry.kind in SPEND_KINDS:
                for token, amount in entry.amounts.items():
                    out[token] += amount
        return out

    @property
    def tokens(self) -> dict[str, int]:
        """What you have now: entered minus spent."""
        entered, spent = self.entered, self.spent_since_entry
        return {t: entered[t] - spent[t] for t in gd.TOKENS}

    @property
    def spent_total(self) -> dict[str, int]:
        """Everything spent this run, across every baseline."""
        out = _zero()
        for entry in self.ledger:
            if entry.kind in SPEND_KINDS:
                for token, amount in entry.amounts.items():
                    out[token] += amount
        return out

    def spent_in_live(self, live: int) -> dict[str, int]:
        out = _zero()
        for entry in self.ledger:
            if entry.kind in SPEND_KINDS and entry.live == live:
                for token, amount in entry.amounts.items():
                    out[token] += amount
        return out

    # --- what has been bought --------------------------------------------

    @property
    def bought(self) -> list[str]:
        return [e.key for e in self.ledger if e.kind == SONG]

    @property
    def courses(self) -> list[Entry]:
        return [e for e in self.ledger if e.kind == COURSE]

    @property
    def courses_this_live(self) -> int:
        return sum(1 for e in self.courses if e.live == self.live)

    @property
    def recent(self) -> list[Entry]:
        """The ledger newest-first, for showing an audit trail."""
        return list(reversed(self.ledger))

    def describe(self, entry: Entry) -> str:
        if entry.kind == SET:
            return "Entered token balance"
        if entry.kind == SONG:
            song = gd.BY_KEY.get(entry.key)
            return f"Bought {song.name}" if song else f"Bought {entry.key}"
        return f"Course{f' ({entry.label})' if entry.label else ''}"

    # --- derived guidance -------------------------------------------------

    def pool(self) -> list[gd.Song]:
        """Every buyable song still available to you right now."""
        return gd.pool_at(self.live, set(self.bought))

    def remaining_targets(self, threshold: float = 0.0) -> list[gd.Song]:
        return gd.worth_buying(self.pool(), threshold)

    def guide_requirement(self, threshold: float = 0.0) -> dict[str, int]:
        return dict(zip(gd.TOKENS, gd.requirement(self.remaining_targets(threshold))))

    def new_this_live_requirement(self, threshold: float = 0.0) -> dict[str, int]:
        bought = set(self.bought)
        fresh = [
            s for s in gd.songs_for_live(self.live)
            if s.key not in bought and (s.net_value or 0) > threshold
        ]
        return dict(zip(gd.TOKENS, gd.requirement(fresh)))

    def spare(self, threshold: float = 0.0) -> dict[str, int]:
        """Tokens beyond what the songs still worth buying will cost.

        This is the number to spend courses out of: the guide's rule is never
        to let a course purchase cost you a high-scoring song, so anything
        above zero here is safe and anything below is already borrowed.
        """
        need, have = self.guide_requirement(threshold), self.tokens
        return {t: have[t] - need[t] for t in gd.TOKENS}

    def spare_total(self, threshold: float = 0.0) -> int:
        """Total safely spendable. Only positives count -- a surplus of Dance
        cannot pay for a course that wants Vocal."""
        return sum(v for v in self.spare(threshold).values() if v > 0)

    def shortfall(self, threshold: float = 0.0) -> dict[str, int]:
        return {t: -v if v < 0 else 0 for t, v in self.spare(threshold).items()}

    def can_afford(self, key: str) -> bool:
        have = self.tokens
        return all(have[t] >= c for t, c in gd.BY_KEY[key].cost_map().items())

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
            "version": 2,
            "live": self.live,
            "ledger": [e.to_dict() for e in self.ledger],
            "finished": self.finished,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunState":
        state = cls()
        state.live = int(data.get("live", 1))
        state.finished = bool(data.get("finished"))

        if "ledger" in data:
            state.ledger = [Entry.from_dict(e) for e in data["ledger"]]
            return state

        # Version 1 stored an already-decremented balance plus flat lists.
        # Replay them as spends, then set the stored balance as the baseline
        # last, so nothing is deducted twice.
        legacy: list[Entry] = []
        for key in data.get("bought", []):
            song = gd.BY_KEY.get(key)
            if song:
                legacy.append(Entry(SONG, song.live, song.cost_map(), key=key))
        for course in data.get("courses", []):
            cost = course.get("cost") or {}
            legacy.append(
                Entry(
                    COURSE,
                    int(course.get("live", 1)),
                    {t: int(cost.get(t, 0)) for t in gd.TOKENS},
                    label=course.get("note", ""),
                )
            )
        stored = data.get("tokens") or {}
        # "mental" was the old key for what Global calls Composure.
        balance = {t: int(stored.get(t, 0)) for t in gd.TOKENS}
        if "mental" in stored:
            balance["composure"] = int(stored["mental"])
        # An all-zero balance means nothing was ever entered, so leave the run
        # without a baseline rather than pretending zero was a real reading.
        if any(balance.values()):
            legacy.append(Entry(SET, state.live, balance))
        state.ledger = legacy
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
    """File the finished run away."""
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
