"""Rebuild the reference tables from the two public sources.

  tracentrial.org  -- the Team Trials tierlist: tier letter + the profile
                      write-up shown when you click an uma, plus the skill
                      catalogue behind those profiles.
  gametora.com     -- per-outfit card data: distance/surface/style aptitudes,
                      stat caps and growth bonuses, Global release dates.

The two join cleanly: tracentrial's `form_id` is GameTora's `card_id`.

Run:  python ingest.py            (refresh everything)
      python ingest.py --dry-run  (fetch and report, write nothing)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import requests

import db

TRACENTRIAL_APP = "6a72f9270fabc276d5b11abc"
TRACENTRIAL_FN = f"https://tracentrial.org/api/apps/{TRACENTRIAL_APP}/functions/{{fn}}"
GAMETORA_MANIFEST = "https://gametora.com/data/manifests/umamusume.json"
GAMETORA_DATA = "https://gametora.com/data/umamusume/{name}.{hash}.json"

UA = {"User-Agent": "Mozilla/5.0 (uma-team-builder; personal use)"}
TIMEOUT = 30

# GameTora stores aptitudes as a fixed-order array. Verified against known
# characters (Smart Falcon dirt A / turf E, Silence Suzuka front A,
# Mejiro McQueen long A / sprint G, Sakura Bakushin O sprint A).
APT_ORDER = ("turf", "dirt", "sprint", "mile", "medium", "long", "front", "pace", "late", "end")
STAT_ORDER = ("speed", "stamina", "power", "guts", "wit")


def tracentrial(fn: str, op: str) -> dict:
    r = requests.post(TRACENTRIAL_FN.format(fn=fn), json={"op": op}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def gametora(name: str):
    manifest = requests.get(GAMETORA_MANIFEST, headers=UA, timeout=TIMEOUT).json()
    if name not in manifest:
        raise KeyError(f"{name!r} is not in the GameTora manifest; keys change over time")
    url = GAMETORA_DATA.format(name=name, hash=manifest[name])
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def clean(value):
    """Normalise the empty strings tracentrial uses for 'no value'."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def build_card_rows(cards: list[dict], umas: list[dict]) -> tuple[list[tuple], list[str]]:
    by_form = {str(u["form_id"]): u for u in umas}
    rows: list[tuple] = []
    warnings: list[str] = []

    for card in cards:
        card_id = int(card["card_id"])
        uma = by_form.get(str(card_id)) or {}
        apt = dict(zip(APT_ORDER, card.get("aptitude") or []))
        if len(apt) != len(APT_ORDER):
            warnings.append(f"card {card_id}: unexpected aptitude array, skipped")
            continue

        base = dict(zip(STAT_ORDER, card.get("base_stats") or [None] * 5))
        mx = dict(zip(STAT_ORDER, card.get("five_star_stats") or [None] * 5))
        growth = dict(zip(STAT_ORDER, card.get("stat_bonus") or [0] * 5))

        base_name = (card.get("name_en") or "").strip()
        title = clean(card.get("title_en_gl")) or clean(card.get("title"))
        if title:
            title = title.strip("[]").strip()

        # Prefer tracentrial's naming for cards it rates -- it matches how the
        # Global community refers to them ("TM Opera O New Year").
        name = clean(uma.get("name")) or (f"{base_name} ({title})" if title else base_name)

        rows.append(
            (
                card_id,
                int(card["char_id"]),
                name,
                base_name,
                title,
                card.get("rarity"),
                clean(card.get("obtained")),
                1 if card.get("release_en") else 0,
                clean(card.get("release_en")),
                uma.get("image_url"),
                clean(card.get("url_name")),
                *[apt[k] for k in APT_ORDER],
                *[base[k] for k in STAT_ORDER],
                *[mx[k] for k in STAT_ORDER],
                *[growth[k] or 0 for k in STAT_ORDER],
                clean(uma.get("tier")),
                clean(uma.get("description")),
                clean(uma.get("unique_skill_id")),
                clean(uma.get("updated_date")),
            )
        )

    unmatched = set(by_form) - {str(c["card_id"]) for c in cards}
    if unmatched:
        warnings.append(
            f"{len(unmatched)} tierlist entries had no GameTora card: {sorted(unmatched)[:5]}"
        )
    return rows, warnings


def build_card_skills(cards: list[dict], umas: list[dict]) -> list[tuple]:
    """Skill links, preferring tracentrial's curated lists for rated cards."""
    by_form = {str(u["form_id"]): u for u in umas}
    out: list[tuple] = []
    seen: set[tuple] = set()

    def add(card_id: int, skill_id, kind: str, slot: int) -> None:
        if skill_id in (None, ""):
            return
        key = (card_id, str(skill_id), kind)
        if key in seen:
            return
        seen.add(key)
        out.append((card_id, str(skill_id), kind, slot))

    for card in cards:
        card_id = int(card["card_id"])
        uma = by_form.get(str(card_id)) or {}
        add(card_id, uma.get("unique_skill_id"), "unique", 0)
        for i, s in enumerate(uma.get("innate_skill_ids") or []):
            add(card_id, s, "innate", i)
        # tracentrial labels these P2..P5 in the UI, so slot 0 is P2.
        for i, s in enumerate(uma.get("potential_skill_ids") or []):
            add(card_id, s, "potential", i)
        for i, s in enumerate(card.get("skills_unique") or []):
            add(card_id, s, "unique", i)
        for i, s in enumerate(card.get("skills_innate") or []):
            add(card_id, s, "innate", i)
        awakening = card.get("skills_awakening_en") or card.get("skills_awakening") or []
        for i, s in enumerate(awakening):
            add(card_id, s, "awakening", i)
        for i, s in enumerate(card.get("skills_event") or []):
            add(card_id, s, "event", i)
    return out


def build_skill_rows(skills: list[dict]) -> list[tuple]:
    return [
        (
            str(s["skill_id"]),
            clean(s.get("name")),
            clean(s.get("description")),
            clean(s.get("rarity")),
            clean(s.get("category")),
            clean(s.get("section")),
            1 if s.get("is_unique") else 0,
            1 if s.get("is_evolved") else 0,
            clean(s.get("activation_condition")),
            s.get("skill_point_cost"),
            s.get("duration_seconds"),
            s.get("velocity_value"),
            s.get("acceleration_value"),
            s.get("recovery_value"),
            s.get("debuff_value"),
            json.dumps(s.get("race_phase") or []),
            json.dumps(s.get("running_style") or []),
            json.dumps(s.get("distance") or []),
            json.dumps(s.get("surface") or []),
        )
        for s in skills
        if s.get("skill_id")
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild the uma reference database.")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()

    print("fetching tracentrial tierlist ...", flush=True)
    umas = tracentrial("manageUma", "list")["umas"]
    print(f"  {len(umas)} tierlist entries")

    print("fetching tracentrial skills ...", flush=True)
    skills = tracentrial("manageSkill", "list")["skills"]
    print(f"  {len(skills)} skills")

    print("fetching GameTora character cards ...", flush=True)
    cards = gametora("character-cards")
    print(f"  {len(cards)} cards")

    rows, warnings = build_card_rows(cards, umas)
    skill_rows = build_skill_rows(skills)
    link_rows = build_card_skills(cards, umas)
    for w in warnings:
        print(f"  ! {w}")

    rated = sum(1 for r in rows if r[-4])
    on_global = sum(1 for r in rows if r[7])
    print(f"\n{len(rows)} cards, {on_global} released on Global, {rated} with a tier")
    print(f"{len(skill_rows)} skills, {len(link_rows)} card-skill links")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    conn = db.connect()
    with conn:
        db.rebuild_reference(conn)
        conn.executemany(
            "INSERT INTO uma_cards VALUES (" + ",".join("?" * len(rows[0])) + ")", rows
        )
        conn.executemany(
            "INSERT INTO skills VALUES (" + ",".join("?" * len(skill_rows[0])) + ")", skill_rows
        )
        conn.executemany("INSERT OR IGNORE INTO card_skills VALUES (?,?,?,?)", link_rows)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.executemany(
            "INSERT INTO ingest_log (ran_at, source, rows, note) VALUES (?,?,?,?)",
            [
                (now, "tracentrial:manageUma", len(umas), f"{rated} tiered"),
                (now, "tracentrial:manageSkill", len(skill_rows), None),
                (now, "gametora:character-cards", len(cards), f"{on_global} on Global"),
            ],
        )
    conn.close()
    print(f"\nwrote {db.DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
