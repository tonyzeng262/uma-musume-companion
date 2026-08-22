"""Pull the tracentrial.org Grand Concert guide into the database.

Unlike the tierlist, the guide pages are not served by an API -- the text is
compiled into the site's React bundle. So this reads the bundle and recovers
the structured pieces from it:

  * the Song values tables (Section 03) -- cost, stat return and net value
    per song, per Live;
  * the Live-by-Live blocks (Section 04) -- lesson refresh pattern plus the
    song / course / purchase-timing advice;
  * the prose of every guide section, so the advice is readable in the app.

The bundle filename is content-hashed and changes on every deploy, so the
current one is read out of the site's index.html rather than pinned. Nothing
here depends on the minified variable names, only on the shape of the data.

Run:  python guide_ingest.py [--dry-run]
"""

from __future__ import annotations

import argparse

import json
import re
import sys
from datetime import datetime, timezone

import requests

import db

SITE = "https://tracentrial.org"
UA = {"User-Agent": "Mozilla/5.0 (uma-team-builder; personal use)"}
TIMEOUT = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS guide_section (
    slug     TEXT PRIMARY KEY,
    kicker   TEXT,
    title    TEXT,
    lead     TEXT,
    body     TEXT,
    position INTEGER
);

CREATE TABLE IF NOT EXISTS guide_live (
    live     INTEGER PRIMARY KEY,
    refresh  TEXT,   -- lesson refresh pattern, e.g. "1 2 3 4 4 2 3"
    song     TEXT,
    course   TEXT,
    purchase TEXT
);

CREATE TABLE IF NOT EXISTS guide_song_value (
    live    INTEGER NOT NULL,
    name    TEXT    NOT NULL,
    effect  TEXT,
    cost    TEXT,
    stats   TEXT,
    net     REAL,
    position INTEGER,
    PRIMARY KEY (live, name)
);
"""

# A song-value row as it appears in the bundle, e.g.
#   {name:"Go This Way",effect:"Strength +1, ...",cost:"42",stats:"30",net:"+9"}
VALUE_ROW = re.compile(
    r'\{name:"((?:[^"\\]|\\.)*)",effect:"((?:[^"\\]|\\.)*)",'
    r'cost:"([^"]*)",stats:"([^"]*)",net:"([^"]*)"\}'
)
LIVE_BLOCK = re.compile(r'title:"(Live \d+)",refresh:"([\d ]+)"')
SECTION = re.compile(r'kicker:"((?:[^"\\]|\\.)*)",title:"((?:[^"\\]|\\.)*)",lead:"((?:[^"\\]|\\.)*)"')
CHILDREN = re.compile(r'children:"((?:[^"\\]|\\.)*)"')

# Prose paragraphs are long; button labels and class fragments are not.
MIN_PROSE = 45


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "0": "\0"}
_ESCAPE_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.S)


def unescape(text: str) -> str:
    """Turn a JS string literal body back into plain text.

    Deliberately not `unicode_escape`: the bundle is already decoded UTF-8, and
    that codec would re-read every non-ASCII character as latin-1, turning
    "RUN×RUN!" into mojibake. Only real escape sequences are touched.
    """

    def replace(m: re.Match[str]) -> str:
        seq = m.group(1)
        if seq[0] in "ux" and len(seq) > 1:
            return chr(int(seq[1:], 16))
        return _ESCAPES.get(seq, seq)

    return _ESCAPE_RE.sub(replace, text)


def fetch_bundle() -> tuple[str, str]:
    index = requests.get(SITE + "/", headers=UA, timeout=TIMEOUT)
    index.encoding = "utf-8"
    index.raise_for_status()
    match = re.search(r'src="(/assets/index-[^"]+\.js)"', index.text)
    if not match:
        raise RuntimeError("could not find the app bundle in index.html")
    url = SITE + match.group(1)
    bundle = requests.get(url, headers=UA, timeout=TIMEOUT)
    bundle.encoding = "utf-8"
    bundle.raise_for_status()
    return url, bundle.text


def parse_song_values(src: str) -> list[list[dict]]:
    """Recover the per-Live value tables, in the order they appear.

    Array boundaries are found by shape: a row whose literal is preceded by
    '[' starts a new table. That survives minifier renaming.
    """
    tables: list[list[dict]] = []
    for m in VALUE_ROW.finditer(src):
        row = {
            "name": unescape(m.group(1)),
            "effect": unescape(m.group(2)),
            "cost": m.group(3),
            "stats": m.group(4),
            "net": m.group(5),
        }
        if src[m.start() - 1] == "[" or not tables:
            tables.append([row])
        else:
            tables[-1].append(row)
    return tables


def parse_live_blocks(src: str) -> list[dict]:
    """Section 04's Live blocks: refresh pattern plus three prose sections."""
    out: list[dict] = []
    marks = list(LIVE_BLOCK.finditer(src))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(src), m.end() + 12000)
        block = src[m.end() : end]
        song, course, purchase = block, "", ""
        if ",course:" in block:
            song, rest = block.split(",course:", 1)
            if ",purchase:" in rest:
                course, purchase = rest.split(",purchase:", 1)
            else:
                course = rest
        out.append(
            {
                "live": int(m.group(1).split()[1]),
                "refresh": m.group(2),
                "song": paragraphs(song),
                "course": paragraphs(course),
                "purchase": paragraphs(purchase),
            }
        )
    return out


def paragraphs(fragment: str) -> str:
    """Prose paragraphs inside a JSX fragment, joined with blank lines."""
    seen: list[str] = []
    for m in CHILDREN.finditer(fragment):
        text = unescape(m.group(1)).strip()
        if len(text) >= MIN_PROSE and text not in seen:
            seen.append(text)
    return "\n\n".join(seen)


def parse_sections(src: str) -> list[dict]:
    """Every guide section's heading and prose, in document order."""
    out: list[dict] = []
    marks = list(SECTION.finditer(src))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(src)
        kicker, title, lead = (unescape(m.group(n)) for n in (1, 2, 3))
        slug = re.sub(r"[^a-z0-9]+", "-", f"{kicker} {title}".lower()).strip("-")
        out.append(
            {
                "slug": slug,
                "kicker": kicker,
                "title": title,
                "lead": lead,
                "body": paragraphs(src[m.end() : end]),
                "position": i,
            }
        )
    return out


def to_float(net: str) -> float | None:
    try:
        return float(net.replace("+", "").strip())
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Import the tracentrial guide text.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump", metavar="PATH", help="also write the parsed guide as JSON")
    args = ap.parse_args()

    url, src = fetch_bundle()
    print(f"bundle {url} ({len(src):,} chars)")

    tables = parse_song_values(src)
    lives = parse_live_blocks(src)
    sections = parse_sections(src)

    print(f"  {len(tables)} song-value tables: {[len(t) for t in tables]}")
    print(f"  {len(lives)} Live blocks")
    print(f"  {len(sections)} guide sections")

    if not tables or not lives or not sections:
        print("\n! the bundle layout changed -- nothing recognisable was found")
        return 1

    empty = [s["title"] for s in sections if not s["body"]]
    if empty:
        print(f"  ! {len(empty)} sections parsed with no body: {empty[:4]}")

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            json.dump(
                {"tables": tables, "lives": lives, "sections": sections}, fh,
                ensure_ascii=False, indent=1,
            )
        print(f"  dumped to {args.dump}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    conn = db.connect()
    with conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM guide_section")
        conn.execute("DELETE FROM guide_live")
        conn.execute("DELETE FROM guide_song_value")
        conn.executemany(
            "INSERT INTO guide_section (slug, kicker, title, lead, body, position)"
            " VALUES (:slug, :kicker, :title, :lead, :body, :position)",
            sections,
        )
        conn.executemany(
            "INSERT INTO guide_live (live, refresh, song, course, purchase)"
            " VALUES (:live, :refresh, :song, :course, :purchase)",
            lives,
        )
        rows = [
            {
                "live": i + 1,
                "name": row["name"],
                "effect": row["effect"],
                "cost": row["cost"],
                "stats": row["stats"],
                "net": to_float(row["net"]),
                "position": j,
            }
            for i, table in enumerate(tables)
            for j, row in enumerate(table)
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO guide_song_value"
            " (live, name, effect, cost, stats, net, position)"
            " VALUES (:live, :name, :effect, :cost, :stats, :net, :position)",
            rows,
        )
        conn.execute(
            "INSERT INTO ingest_log (ran_at, source, rows, note) VALUES (?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tracentrial:guide",
                len(rows),
                f"{len(sections)} sections, {len(lives)} lives",
            ),
        )
    conn.close()
    print(f"\nwrote {len(rows)} song values, {len(lives)} Live blocks, {len(sections)} sections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
