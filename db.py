"""SQLite storage for the Uma Musume Team Trials builder.

Two kinds of data live here:
  * reference data, refreshed by ingest.py from public sources
    (GameTora card data + the tracentrial.org Team Trials tierlist);
  * user data -- which cards you own and any aptitude you have raised.

Reference tables are dropped and rebuilt on every ingest; user tables never are.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("UMA_DB_PATH", Path(__file__).parent / "uma.db"))

APTITUDES = ("turf", "dirt", "sprint", "mile", "medium", "long", "front", "pace", "late", "end")
STATS = ("speed", "stamina", "power", "guts", "wit")

# Reference data: wiped and rebuilt by ingest.py.
REFERENCE_SCHEMA = """
CREATE TABLE uma_cards (
    card_id      INTEGER PRIMARY KEY,   -- GameTora card_id == tracentrial form_id
    char_id      INTEGER NOT NULL,      -- shared by every outfit of one character
    name         TEXT    NOT NULL,      -- display name, tracentrial's wording when rated
    base_name    TEXT    NOT NULL,      -- character name without the outfit
    title        TEXT,                  -- outfit title, e.g. "Special Dreamer"
    rarity       INTEGER,
    obtained     TEXT,
    released_global INTEGER NOT NULL DEFAULT 0,
    release_en   TEXT,
    image_url    TEXT,
    url_name     TEXT,
    apt_turf TEXT, apt_dirt TEXT,
    apt_sprint TEXT, apt_mile TEXT, apt_medium TEXT, apt_long TEXT,
    apt_front TEXT, apt_pace TEXT, apt_late TEXT, apt_end TEXT,
    base_speed INTEGER, base_stamina INTEGER, base_power INTEGER,
    base_guts INTEGER, base_wit INTEGER,
    max_speed INTEGER, max_stamina INTEGER, max_power INTEGER,
    max_guts INTEGER, max_wit INTEGER,
    growth_speed INTEGER DEFAULT 0, growth_stamina INTEGER DEFAULT 0,
    growth_power INTEGER DEFAULT 0, growth_guts INTEGER DEFAULT 0,
    growth_wit INTEGER DEFAULT 0,
    tier         TEXT,                  -- S/A/B/C from the tracentrial tierlist, NULL if unrated
    tier_note    TEXT,                  -- the profile write-up shown on the tierlist
    unique_skill_id TEXT,
    tier_updated TEXT
);

CREATE TABLE skills (
    skill_id     TEXT PRIMARY KEY,
    name         TEXT,
    description  TEXT,
    rarity       TEXT,
    category     TEXT,
    section      TEXT,
    is_unique    INTEGER DEFAULT 0,
    is_evolved   INTEGER DEFAULT 0,
    activation_condition TEXT,
    skill_point_cost INTEGER,
    duration_seconds REAL,
    velocity_value REAL, acceleration_value REAL,
    recovery_value REAL, debuff_value REAL,
    race_phase    TEXT,   -- JSON array
    running_style TEXT,   -- JSON array
    distance      TEXT,   -- JSON array
    surface       TEXT    -- JSON array
);

CREATE TABLE card_skills (
    card_id  INTEGER NOT NULL,
    skill_id TEXT    NOT NULL,
    kind     TEXT    NOT NULL,   -- unique | innate | potential | awakening | event
    slot     INTEGER,            -- position within the kind, where it is meaningful
    PRIMARY KEY (card_id, skill_id, kind)
);

CREATE INDEX idx_card_skills_card ON card_skills(card_id);
CREATE INDEX idx_cards_char ON uma_cards(char_id);

CREATE TABLE ingest_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at    TEXT NOT NULL,
    source    TEXT NOT NULL,
    rows      INTEGER,
    note      TEXT
);
"""

# User data: created once, never dropped.
USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS roster (
    card_id  INTEGER PRIMARY KEY,
    owned    INTEGER NOT NULL DEFAULT 1,
    note     TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);

-- Aptitudes you have raised in game (inheritance, trained). Only the grades that
-- differ from the card's base need a row; anything missing falls back to the card.
CREATE TABLE IF NOT EXISTS roster_aptitude_override (
    card_id  INTEGER NOT NULL,
    aptitude TEXT    NOT NULL,   -- one of APTITUDES
    grade    TEXT    NOT NULL,   -- S A B C D E F G
    PRIMARY KEY (card_id, aptitude)
);

CREATE TABLE IF NOT EXISTS saved_team (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    payload    TEXT NOT NULL   -- JSON of the solved team
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(USER_SCHEMA)
    return conn


def rebuild_reference(conn: sqlite3.Connection) -> None:
    """Drop and recreate the reference tables, leaving user tables untouched."""
    for table in ("card_skills", "skills", "uma_cards", "ingest_log"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.executescript(REFERENCE_SCHEMA)


def has_reference_data(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT count(*) AS n FROM sqlite_master WHERE type='table' AND name='uma_cards'"
    ).fetchone()
    if not row or not row["n"]:
        return False
    return bool(conn.execute("SELECT count(*) AS n FROM uma_cards").fetchone()["n"])
