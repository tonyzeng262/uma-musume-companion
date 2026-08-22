"""Add umas to your roster by uploading screenshots of your roster screen.

Matching is done entirely offline against GameTora's reference portraits (see
portraits.py). Because no two people's screenshots are cropped the same way,
the grid is adjustable and every match is shown for approval before anything
is written -- the matcher proposes, you confirm.
"""

from __future__ import annotations

import sqlite3

import streamlit as st
from PIL import Image

import portraits
import roster

STATE_KEY = "screenshot_matches"


def _index(conn: sqlite3.Connection, only_global: bool):
    return portraits.Index.build(conn, only_global=only_global)


def render(conn: sqlite3.Connection) -> None:
    st.subheader("Import from screenshots")

    on_disk = len(list(portraits.ICON_DIR.glob("*.png"))) if portraits.ICON_DIR.exists() else 0
    if on_disk == 0:
        st.warning("No reference portraits downloaded yet.")
        if st.button("Download reference portraits (about 2 MB)", type="primary"):
            bar = st.progress(0.0, text="Downloading...")
            fetched, missing = portraits.sync_icons(
                conn, progress=lambda frac, f, m: bar.progress(frac, text=f"{f} downloaded, {m} unavailable")
            )
            st.success(f"Downloaded {fetched} portraits ({missing} unavailable).")
            st.rerun()
        return

    st.caption(
        f"{on_disk} reference portraits on disk. Matching runs locally -- no "
        "image is uploaded anywhere."
    )

    uploads = st.file_uploader(
        "Screenshots of your uma list",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        help="Crop to just the grid of portraits if you can; otherwise use the "
             "region sliders below to cut off the header and navigation bar.",
    )
    if not uploads:
        st.info(
            "Upload one or more screenshots. Best results come from the roster "
            "grid with whole tiles visible -- avoid half-scrolled rows."
        )
        return

    only_global = st.checkbox(
        "Match against Global cards only", value=True,
        help="Leave on unless you play a server where unreleased cards exist.",
    )

    with st.expander("Grid settings", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            rows = st.number_input("Rows of portraits", 1, 12, 3, 1)
            cols = st.number_input("Columns of portraits", 1, 12, 5, 1)
            inset = st.slider(
                "Trim each tile's border", 0.0, 0.35, 0.10, 0.01,
                help="Cuts off the frame, level text and rarity stars the game "
                     "draws around the artwork.",
            )
        with c2:
            top = st.slider("Crop from top", 0.0, 0.9, 0.0, 0.01)
            bottom = st.slider("Crop from bottom", 0.0, 0.9, 0.0, 0.01)
            left = st.slider("Crop from left", 0.0, 0.9, 0.0, 0.01)
            right = st.slider("Crop from right", 0.0, 0.9, 0.0, 0.01)
        box = (left, top, 1.0 - right, 1.0 - bottom)
        min_conf = st.slider(
            "Only pre-select matches above this confidence", 0.0, 1.0, 0.55, 0.05
        )

    first = Image.open(uploads[0]).convert("RGB")
    preview, guess_col = st.columns([3, 1])
    with preview:
        st.image(portraits.crop_region(first, box), caption="Region that will be split into tiles", width="stretch")
    with guess_col:
        if st.button("Guess the grid", width="stretch"):
            r, c = portraits.guess_grid(first, box)
            st.session_state["_grid_guess"] = (r, c)
            st.info(f"Looks like {r} x {c}. Set the numbers above to match.")
        if "_grid_guess" in st.session_state:
            r, c = st.session_state["_grid_guess"]
            st.caption(f"Last guess: {r} rows x {c} columns")

    if st.button("Identify umas", type="primary", width="stretch"):
        index = _index(conn, only_global)
        if not len(index):
            st.error("No reference portraits matched the current filter.")
            return
        found = []
        for shot in uploads:
            img = Image.open(shot).convert("RGB")
            for (r, c), tile in portraits.tiles(img, int(rows), int(cols), box, inset):
                matches = index.match(tile, top_k=5)
                if matches:
                    found.append(
                        {
                            "source": shot.name,
                            "cell": f"r{r + 1}c{c + 1}",
                            "options": [(m.card_id, m.name, m.confidence) for m in matches],
                            "chosen": matches[0].card_id,
                            "accept": matches[0].confidence >= min_conf,
                            "confidence": matches[0].confidence,
                        }
                    )
        st.session_state[STATE_KEY] = found
        st.rerun()

    found = st.session_state.get(STATE_KEY)
    if not found:
        return

    st.divider()
    accepted = [f for f in found if f["accept"]]
    st.markdown(f"#### {len(found)} tiles read, {len(accepted)} pre-selected")
    st.caption(
        "Uncheck anything that is not actually an uma (empty slots, buttons), "
        "and correct any wrong name before adding."
    )

    names = {r["card_id"]: r["name"] for r in roster.all_cards(conn, global_only=False)}
    per_row = 3
    for start in range(0, len(found), per_row):
        cols_ui = st.columns(per_row)
        for col, item in zip(cols_ui, found[start : start + per_row]):
            with col:
                idx = found.index(item)
                item["accept"] = st.checkbox(
                    f"{item['cell']} · {item['confidence']:.0%} confident",
                    value=item["accept"],
                    key=f"acc_{idx}",
                )
                options = [cid for cid, _, _ in item["options"]]
                labels = {
                    cid: f"{name}  ({conf:.0%})" for cid, name, conf in item["options"]
                }
                item["chosen"] = st.selectbox(
                    "Match",
                    options=options,
                    index=options.index(item["chosen"]) if item["chosen"] in options else 0,
                    format_func=lambda c: labels.get(c, names.get(c, str(c))),
                    key=f"sel_{idx}",
                    label_visibility="collapsed",
                )
                if item["confidence"] < min_conf:
                    st.caption("low confidence - check this one")

    chosen_ids = sorted({f["chosen"] for f in found if f["accept"]})
    st.markdown(f"**{len(chosen_ids)} umas will be added.**")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Add to my roster", type="primary", width="stretch", disabled=not chosen_ids):
            merged = sorted(set(roster.owned_ids(conn)) | set(chosen_ids))
            roster.set_owned(conn, merged)
            st.session_state.pop(STATE_KEY, None)
            st.success(f"Roster now has {len(merged)} umas.")
            st.rerun()
    with c2:
        if st.button("Discard these results", width="stretch"):
            st.session_state.pop(STATE_KEY, None)
            st.rerun()
