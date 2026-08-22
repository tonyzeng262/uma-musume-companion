"""Accuracy check for the offline portrait matcher.

There is no library of real screenshots to test against, so this degrades the
reference portraits the way a game tile degrades them -- rescaled, composited
onto a coloured background, framed, and with a badge in the corner -- and
measures how often the matcher still picks the right uma. Real screenshots will
be harder than this; treat the numbers as an upper bound.

Run: python test_portraits.py
"""

from __future__ import annotations

import io
import random
import sys

import numpy as np
from PIL import Image, ImageDraw

import db
import portraits


def fake_tile(path, seed: int, size: int = 112) -> Image.Image:
    """Approximate what a roster tile does to the reference artwork."""
    rng = random.Random(seed)
    with Image.open(path) as src:
        art = portraits._flatten(src).resize((size, size), Image.LANCZOS)

    # Coloured background wash, as the game composites behind the portrait.
    bg = Image.new("RGB", (size, size), (rng.randint(200, 255),) * 3)
    grad = np.linspace(0, 40, size, dtype=np.float32)[:, None]
    tint = np.asarray(bg, dtype=np.float32)
    tint[..., rng.randint(0, 2)] = np.clip(tint[..., rng.randint(0, 2)] - grad, 0, 255)
    bg = Image.fromarray(tint.astype(np.uint8))
    tile = Image.blend(bg, art, 0.88)

    # Frame plus a corner badge, roughly where level and rarity are drawn.
    draw = ImageDraw.Draw(tile)
    draw.rectangle([0, 0, size - 1, size - 1], outline=(240, 220, 140), width=3)
    draw.rectangle([2, size - 18, 44, size - 3], fill=(30, 30, 40))
    draw.rectangle([size - 30, 2, size - 3, 16], fill=(220, 180, 60))

    # A little resampling and compression noise.
    tile = tile.resize((int(size * 0.85), int(size * 0.85)), Image.BILINEAR)
    buf = io.BytesIO()
    tile.save(buf, format="JPEG", quality=78)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def main() -> int:
    conn = db.connect()
    index = portraits.Index.build(conn, only_global=True)
    print(f"reference set: {len(index)} Global portraits")
    if len(index) < 50:
        print("FAIL  not enough portraits on disk -- run sync_icons first")
        return 1

    rows = list(conn.execute("SELECT card_id, name FROM uma_cards WHERE released_global = 1"))
    top1 = top3 = 0
    misses: list[str] = []
    confidences: list[float] = []

    for i, row in enumerate(rows):
        path = portraits.icon_path(row["card_id"])
        if not path.exists():
            continue
        matches = index.match(fake_tile(path, seed=i), top_k=3)
        ids = [m.card_id for m in matches]
        confidences.append(matches[0].confidence)
        if ids and ids[0] == row["card_id"]:
            top1 += 1
        else:
            misses.append(f"{row['name']} -> {matches[0].name if matches else 'none'}")
        if row["card_id"] in ids:
            top3 += 1

    n = len(confidences)
    print(f"\ntop-1 accuracy : {top1}/{n}  ({100 * top1 / n:.1f}%)")
    print(f"top-3 accuracy : {top3}/{n}  ({100 * top3 / n:.1f}%)")
    print(f"median confidence on the top hit: {sorted(confidences)[n // 2]:.2f}")
    if misses:
        print(f"\nmissed {len(misses)}:")
        for m in misses[:10]:
            print("  " + m)

    # A distinct portrait should be clearly closer than an unrelated one.
    a, b = rows[0], rows[len(rows) // 2]
    tile = fake_tile(portraits.icon_path(a["card_id"]), seed=99)
    ranked = index.match(tile, top_k=len(index))
    right = next(m.distance for m in ranked if m.card_id == a["card_id"])
    wrong = next(m.distance for m in ranked if m.card_id == b["card_id"])
    print(f"\nseparation check: correct={right:.3f} vs unrelated={wrong:.3f}")

    ok = top1 / n >= 0.80
    print("\nPASS  matcher is usable on synthetic tiles" if ok
          else "\nFAIL  accuracy too low to ship as an automatic import")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
