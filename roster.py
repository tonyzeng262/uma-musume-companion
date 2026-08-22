"""Reading cards out of the database and tracking which ones you own."""

from __future__ import annotations

import json
import sqlite3

import db
from optimizer import CardView

APTITUDES = db.APTITUDES
STATS = db.STATS


def _to_view(row: sqlite3.Row, overrides: dict[str, str] | None = None) -> CardView:
    aptitude = {k: (row[f"apt_{k}"] or "G") for k in APTITUDES}
    if overrides:
        aptitude.update(overrides)
    return CardView(
        card_id=row["card_id"],
        name=row["name"],
        tier=row["tier"],
        tier_note=row["tier_note"],
        image_url=row["image_url"],
        aptitude=aptitude,
        max_stats={s: row[f"max_{s}"] or 0 for s in STATS},
        growth={s: row[f"growth_{s}"] or 0 for s in STATS},
    )


def all_cards(conn: sqlite3.Connection, global_only: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM uma_cards"
    if global_only:
        sql += " WHERE released_global = 1"
    sql += " ORDER BY name"
    return list(conn.execute(sql))


def card(conn: sqlite3.Connection, card_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM uma_cards WHERE card_id = ?", (card_id,)).fetchone()


def overrides(conn: sqlite3.Connection) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for row in conn.execute("SELECT card_id, aptitude, grade FROM roster_aptitude_override"):
        out.setdefault(row["card_id"], {})[row["aptitude"]] = row["grade"]
    return out


def owned_ids(conn: sqlite3.Connection) -> list[int]:
    return [r["card_id"] for r in conn.execute("SELECT card_id FROM roster WHERE owned = 1")]


def owned_views(conn: sqlite3.Connection) -> list[CardView]:
    """The umas you own, with any raised aptitudes already applied."""
    ov = overrides(conn)
    rows = conn.execute(
        "SELECT c.* FROM uma_cards c JOIN roster r ON r.card_id = c.card_id"
        " WHERE r.owned = 1 ORDER BY c.name"
    )
    return [_to_view(row, ov.get(row["card_id"])) for row in rows]


def set_owned(conn: sqlite3.Connection, card_ids: list[int]) -> None:
    """Replace the roster with exactly `card_ids`."""
    with conn:
        conn.execute("DELETE FROM roster")
        conn.executemany(
            "INSERT INTO roster (card_id, owned) VALUES (?, 1)", [(int(c),) for c in card_ids]
        )


def set_override(conn: sqlite3.Connection, card_id: int, aptitude: str, grade: str | None) -> None:
    with conn:
        if grade is None:
            conn.execute(
                "DELETE FROM roster_aptitude_override WHERE card_id = ? AND aptitude = ?",
                (card_id, aptitude),
            )
        else:
            conn.execute(
                "INSERT INTO roster_aptitude_override (card_id, aptitude, grade) VALUES (?,?,?)"
                " ON CONFLICT(card_id, aptitude) DO UPDATE SET grade = excluded.grade",
                (card_id, aptitude, grade),
            )


def clear_overrides(conn: sqlite3.Connection, card_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM roster_aptitude_override WHERE card_id = ?", (card_id,))


def skills_for(conn: sqlite3.Connection, card_id: int) -> dict[str, list[sqlite3.Row]]:
    rows = conn.execute(
        "SELECT cs.kind, cs.slot, s.* FROM card_skills cs"
        " LEFT JOIN skills s ON s.skill_id = cs.skill_id"
        " WHERE cs.card_id = ? ORDER BY cs.kind, cs.slot",
        (card_id,),
    )
    out: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        out.setdefault(row["kind"], []).append(row)
    return out


def save_team(conn: sqlite3.Connection, name: str, payload: dict) -> None:
    with conn:
        conn.execute(
            "INSERT INTO saved_team (name, payload) VALUES (?, ?)", (name, json.dumps(payload))
        )


def saved_teams(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM saved_team ORDER BY created_at DESC"))
