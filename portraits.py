"""Offline portrait matching: work out which umas are in a screenshot.

GameTora publishes a 128x128 bust portrait for every card -- the same artwork
the game uses on its roster tiles. So identifying an uma in a screenshot is a
nearest-neighbour lookup: hash each tile, hash every reference portrait, take
the closest.

Two signatures are compared, because either alone is fooled easily:

  * a **difference hash** of the greyscale image (structure: hair silhouette,
    pose, where the light and dark regions fall). Robust to scale and
    brightness, blind to colour -- it would confuse two umas with the same
    outline.
  * a small **colour grid** (hair and outfit colour), which separates those,
    but is thrown off by whatever background the game composites behind the
    portrait.

Structure is weighted more heavily for that reason. Both are computed on a
centre crop, since game tiles put level text, stars and rank badges around
the edges.

No network access is needed at match time; `sync_icons` downloads the
reference set once.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
from PIL import Image

ICON_DIR = Path(__file__).parent / "assets" / "icons"
ICON_URL = "https://gametora.com/images/umamusume/characters/thumb/chara_stand_{char}_{card}.png"
UA = {"User-Agent": "Mozilla/5.0 (uma-team-builder; personal use)"}

HASH_SIZE = 16          # dHash grid -> HASH_SIZE * (HASH_SIZE-1) bits
COLOR_GRID = 6          # colour signature resolution
CENTER_CROP = 0.78      # fraction of the tile kept before hashing
STRUCTURE_WEIGHT = 0.72  # vs colour; structure is the more reliable signal


@dataclass
class Match:
    card_id: int
    name: str
    distance: float      # 0 = identical, 1 = nothing in common

    @property
    def confidence(self) -> float:
        return max(0.0, min(1.0, 1.0 - self.distance / 0.45))


def center_crop(img: Image.Image, fraction: float = CENTER_CROP) -> Image.Image:
    w, h = img.size
    dw, dh = w * (1 - fraction) / 2, h * (1 - fraction) / 2
    return img.crop((int(dw), int(dh), int(w - dw), int(h - dh)))


def _flatten(img: Image.Image) -> Image.Image:
    """Composite transparency onto white, the way the reference art is drawn."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


def structure_hash(img: Image.Image) -> np.ndarray:
    # Standard dHash: one column wider than tall, then compare each pixel with
    # its right-hand neighbour -> HASH_SIZE * HASH_SIZE bits.
    grey = center_crop(_flatten(img)).convert("L").resize(
        (HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS
    )
    a = np.asarray(grey, dtype=np.int16)
    return (a[:, 1:] > a[:, :-1]).flatten()


def color_signature(img: Image.Image) -> np.ndarray:
    small = center_crop(_flatten(img)).resize((COLOR_GRID, COLOR_GRID), Image.LANCZOS)
    return np.asarray(small, dtype=np.float32).reshape(-1) / 255.0


def signature(img: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    return structure_hash(img), color_signature(img)


def distance(
    a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray]
) -> float:
    structure = float(np.mean(a[0] != b[0]))
    color = float(np.mean(np.abs(a[1] - b[1])))
    return STRUCTURE_WEIGHT * structure + (1 - STRUCTURE_WEIGHT) * color


# --- reference set --------------------------------------------------------

def icon_path(card_id: int) -> Path:
    return ICON_DIR / f"{card_id}.png"


def sync_icons(
    conn: sqlite3.Connection, only_global: bool = False, progress=None
) -> tuple[int, int]:
    """Download any reference portraits not already on disk. (fetched, missing)"""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    sql = "SELECT card_id, char_id FROM uma_cards"
    if only_global:
        sql += " WHERE released_global = 1"
    rows = list(conn.execute(sql))
    fetched = missing = 0
    session = requests.Session()
    session.headers.update(UA)
    for i, row in enumerate(rows):
        path = icon_path(row["card_id"])
        if path.exists() and path.stat().st_size > 0:
            continue
        url = ICON_URL.format(char=row["char_id"], card=row["card_id"])
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200 and resp.content[:4] == b"\x89PNG":
                path.write_bytes(resp.content)
                fetched += 1
            else:
                missing += 1
        except requests.RequestException:
            missing += 1
        if progress:
            progress((i + 1) / len(rows), fetched, missing)
    return fetched, missing


class Index:
    """Signatures for every reference portrait on disk."""

    def __init__(self, entries: list[tuple[int, str, tuple[np.ndarray, np.ndarray]]]):
        self.entries = entries

    @classmethod
    def build(cls, conn: sqlite3.Connection, only_global: bool = True) -> "Index":
        sql = "SELECT card_id, name FROM uma_cards"
        if only_global:
            sql += " WHERE released_global = 1"
        entries = []
        for row in conn.execute(sql):
            path = icon_path(row["card_id"])
            if not path.exists():
                continue
            with Image.open(path) as img:
                entries.append((row["card_id"], row["name"], signature(img)))
        return cls(entries)

    def __len__(self) -> int:
        return len(self.entries)

    def match(self, tile: Image.Image, top_k: int = 5) -> list[Match]:
        if not self.entries:
            return []
        sig = signature(tile)
        scored = [
            Match(card_id, name, distance(sig, ref)) for card_id, name, ref in self.entries
        ]
        scored.sort(key=lambda m: m.distance)
        return scored[:top_k]


# --- carving a screenshot into tiles --------------------------------------

def crop_region(img: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    """Crop by fractional (left, top, right, bottom) so settings survive rescaling."""
    w, h = img.size
    left, top, right, bottom = box
    return img.crop((int(left * w), int(top * h), int(right * w), int(bottom * h)))


def tiles(
    img: Image.Image,
    rows: int,
    cols: int,
    box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    inset: float = 0.0,
) -> list[tuple[tuple[int, int], Image.Image]]:
    """Split a region into a rows x cols grid.

    `inset` trims each cell's own border, which is where the game draws frames
    and badges.
    """
    region = crop_region(img, box)
    w, h = region.size
    cell_w, cell_h = w / cols, h / rows
    pad_x, pad_y = cell_w * inset, cell_h * inset
    out = []
    for r in range(rows):
        for c in range(cols):
            left = c * cell_w + pad_x
            top = r * cell_h + pad_y
            cell = region.crop(
                (int(left), int(top), int(left + cell_w - 2 * pad_x), int(top + cell_h - 2 * pad_y))
            )
            if cell.size[0] > 4 and cell.size[1] > 4:
                out.append(((r, c), cell))
    return out


def guess_grid(
    img: Image.Image, box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
) -> tuple[int, int]:
    """Estimate the grid size from repeating structure in the image.

    Roster screens draw evenly spaced tiles, so the edge energy projected onto
    each axis is roughly periodic. The strongest autocorrelation peak gives the
    tile pitch, and the region size divided by the pitch gives the count. This
    is a starting guess only -- it is meant to be corrected in the UI.
    """
    region = crop_region(img, box).convert("L")
    a = np.asarray(region.resize((min(region.width, 600), min(region.height, 600))), dtype=np.float32)

    def period(energy: np.ndarray, span: int) -> int:
        energy = energy - energy.mean()
        if not np.any(energy):
            return span
        corr = np.correlate(energy, energy, mode="full")[len(energy) - 1:]
        lo = max(8, span // 12)
        hi = max(lo + 1, span // 2)
        window = corr[lo:hi]
        if not len(window):
            return span
        return int(lo + int(np.argmax(window)))

    dx = np.abs(np.diff(a, axis=1)).sum(axis=0)
    dy = np.abs(np.diff(a, axis=0)).sum(axis=1)
    cols = max(1, round(a.shape[1] / max(1, period(dx, a.shape[1]))))
    rows = max(1, round(a.shape[0] / max(1, period(dy, a.shape[0]))))
    return min(rows, 12), min(cols, 12)
