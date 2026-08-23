"""The Grand Live run tracker, song value board, and guide reader."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

import grandlive as gl
import grandlive_data as gd

POSITIVE = "#2e7d4f"
NEGATIVE = "#c1373a"
NEUTRAL = "#8a8a8a"
TOKEN_COLOR = gd.TOKEN_COLOR


def value_color(net: float | None) -> str:
    if net is None:
        return NEUTRAL
    if net > 20:
        return POSITIVE
    if net > 0:
        return "#6a9b46"
    if net == 0:
        return NEUTRAL
    return NEGATIVE


def cost_chips(cost: dict[str, int]) -> str:
    parts = [
        f"<span style='display:inline-block;margin:1px 4px 1px 0;padding:1px 8px;"
        f"border-radius:9px;background:{TOKEN_COLOR[t]};color:#fff;font-size:11px'>"
        f"{gd.TOKEN_SHORT[t]} {v}</span>"
        for t, v in cost.items()
        if v
    ]
    return "".join(parts) or "<span style='color:#888;font-size:11px'>free</span>"


def token_swatch(token: str) -> str:
    return (
        f"<span style='display:inline-block;width:9px;height:9px;border-radius:2px;"
        f"background:{TOKEN_COLOR[token]};margin-right:5px'></span>"
    )


def token_row(values: dict[str, int], caption: str, danger: bool = False) -> None:
    """One line of five coloured token figures."""
    st.caption(caption)
    cols = st.columns(5)
    for col, token in zip(cols, gd.TOKENS):
        with col:
            value = values[token]
            color = NEGATIVE if (danger and value < 0) else "inherit"
            st.markdown(
                f"{token_swatch(token)}<span style='font-size:12px;color:#888'>"
                f"{gd.TOKEN_LABELS[token]}</span><br>"
                f"<span style='font-size:22px;font-weight:600;color:{color}'>{value}</span>",
                unsafe_allow_html=True,
            )


def net_badge(net: float | None) -> str:
    if net is None:
        return ""
    sign = "+" if net > 0 else ""
    return (
        f"<span style='display:inline-block;padding:1px 9px;border-radius:4px;"
        f"background:{value_color(net)};color:#fff;font-weight:700;font-size:12px'>"
        f"{sign}{net:g}</span>"
    )


# --- the run tracker ------------------------------------------------------

def render_run(conn: sqlite3.Connection) -> None:
    run = gl.load(conn)

    def commit() -> None:
        gl.save(conn, run)
        st.rerun()

    if run.finished:
        st.success("Run finished. Archive it or start a fresh one below.")

    head, controls = st.columns([3, 2])
    with head:
        st.subheader(gd.LIVE_LABELS[run.live])
        guide = conn.execute(
            "SELECT refresh FROM guide_live WHERE live = ?", (run.live,)
        ).fetchone()
        if guide and guide["refresh"]:
            st.caption(f"Lesson refresh pattern: **{guide['refresh']}**")
    with controls:
        c1, c2 = st.columns(2)
        with c1:
            label = "Finish run" if run.live >= gl.LAST_LIVE else f"End Live {run.live}"
            if st.button(label, type="primary", width="stretch"):
                run.advance_live()
                commit()
        with c2:
            if st.button("Undo", width="stretch", disabled=not run.can_undo):
                run.undo()
                commit()

    songs_note = (
        f"{run.songs_learned} songs learned (including the 2 free ones). "
        f"Grand Success needs {gd.GRAND_SUCCESS_SONGS}."
    )
    (st.success if run.on_track_for_grand_success else st.info)(songs_note)

    st.divider()

    # --- coins ------------------------------------------------------------
    need_new = run.new_this_live_requirement()
    need_all = run.guide_requirement()

    left, right = st.columns(2)
    with left:
        st.markdown("#### What the guide says you need")
        token_row(need_new, f"Songs worth buying that are new in Live {run.live}")
        if sum(need_all.values()) != sum(need_new.values()):
            st.write("")
            token_row(need_all, "Everything still worth buying, including carry-overs")
        quoted = gd.TRACENTRIAL_REQUIREMENTS.get(run.live)
        if quoted:
            st.caption(
                "tracentrial quotes "
                + " / ".join(str(v) for v in quoted)
                + " for this Live at full pool."
            )

    with right:
        st.markdown("#### What you actually have")
        # The widget key carries the ledger length so the boxes re-initialise
        # from the saved baseline after any action -- otherwise Streamlit keeps
        # showing whatever was typed last, which would drift from the truth.
        stamp = len(run.ledger)
        with st.form("tokens"):
            st.caption(
                "Type what the game is showing you. It is saved as a fixed "
                "baseline; songs and courses are then subtracted from it."
            )
            cols = st.columns(5)
            entered = {}
            for col, token in zip(cols, gd.TOKENS):
                with col:
                    entered[token] = st.number_input(
                        gd.TOKEN_LABELS[token],
                        min_value=0,
                        max_value=9999,
                        value=int(run.entered[token]),
                        step=1,
                        key=f"tok_{token}_{stamp}",
                    )
            if st.form_submit_button("Save as my current balance", width="stretch"):
                run.set_tokens(entered)
                commit()

        if not run.has_baseline:
            st.info("Enter your token counts above to start tracking spending.")
        else:
            token_row(run.tokens, "Have now (baseline minus everything spent)", danger=True)
            spent = run.spent_since_entry
            if any(spent.values()):
                st.caption(
                    "Spent since you entered it: "
                    + ", ".join(
                        f"**{v} {gd.TOKEN_LABELS[t]}**" for t, v in spent.items() if v
                    )
                    + f"  ({sum(spent.values())} tokens total)"
                )
            else:
                st.caption("Nothing spent since you entered it.")

        short = run.shortfall()
        if any(short.values()):
            st.warning(
                "Short by "
                + ", ".join(f"{v} {gd.TOKEN_LABELS[t]}" for t, v in short.items() if v)
            )
        elif run.has_baseline:
            st.success("You can afford every song still worth buying.")
        if run.overspent():
            st.error(
                "A token count has gone negative. Either a purchase was logged "
                "twice, or the baseline was stale -- use Undo, or re-enter what "
                "the game is showing to start a fresh baseline."
            )

    st.divider()

    # --- songs ------------------------------------------------------------
    st.markdown("#### Songs available now")
    st.caption(
        "Sorted by the guide's net value. Buying one subtracts its cost from "
        "both your tokens and the requirement above."
    )
    pool = sorted(run.pool(), key=lambda s: -(s.net_value or 0))
    for song in pool:
        c1, c2, c3, c4 = st.columns([4, 3, 2, 1.4])
        with c1:
            st.markdown(f"**{song.name}**", unsafe_allow_html=True)
            label = f"Live {song.live} - {song.effect}"
            if song.alt_name:
                label += f"  ·  guide calls it “{song.alt_name}”"
            st.caption(label)
        with c2:
            st.markdown(cost_chips(song.cost_map()), unsafe_allow_html=True)
            st.caption(f"{song.total_cost} tokens total")
        with c3:
            st.markdown(net_badge(song.net_value), unsafe_allow_html=True)
        with c4:
            afford = run.can_afford(song.key)
            if st.button(
                "Buy" if afford else "Buy anyway",
                key=f"buy_{song.key}",
                width="stretch",
                type="primary" if afford and (song.net_value or 0) > 20 else "secondary",
            ):
                run.buy_song(song.key)
                commit()

    if run.bought:
        with st.expander(f"Bought this run ({len(run.bought)})"):
            for key in run.bought:
                song = gd.BY_KEY[key]
                c1, c2 = st.columns([5, 1])
                c1.markdown(
                    f"**{song.name}** - {song.effect}  {net_badge(song.net_value)}",
                    unsafe_allow_html=True,
                )
                if c2.button("Refund", key=f"unbuy_{key}", width="stretch"):
                    run.unbuy_song(key)
                    commit()

    st.divider()

    # --- courses ----------------------------------------------------------
    st.markdown("#### Live Technique courses")
    hint = gd.COURSE_COST_HINT.get(run.live, 16)
    st.caption(
        f"Enter what the course actually cost you. The guide expects roughly "
        f"{hint} tokens per course in this Live, and warns against courses "
        f"above that -- and against PT courses entirely."
    )
    with st.form("course", clear_on_submit=True):
        cols = st.columns(6)
        spend = {}
        for col, token in zip(cols, gd.TOKENS):
            with col:
                spend[token] = st.number_input(
                    gd.TOKEN_LABELS[token], min_value=0, max_value=999, value=0,
                    step=1, key=f"course_{token}",
                )
        with cols[5]:
            note = st.text_input("Note", value="", key="course_note")
        if st.form_submit_button("Take this course", width="stretch"):
            if sum(spend.values()) == 0:
                st.warning("Enter the token cost first.")
            else:
                run.take_course(spend, note)
                commit()

    taken = [c for c in run.courses if c.live == run.live]
    if taken:
        st.caption(f"{len(taken)} courses taken in Live {run.live}")
        st.dataframe(
            pd.DataFrame(
                [
                    {gd.TOKEN_LABELS[t]: c.amounts[t] for t in gd.TOKENS}
                    | {"Total": sum(c.amounts.values()), "Note": c.label}
                    for c in taken
                ]
            ),
            width="stretch",
            hide_index=True,
        )

    # --- audit trail ------------------------------------------------------
    if run.ledger:
        spent_live = run.spent_in_live(run.live)
        with st.expander(
            f"Everything logged this run ({len(run.ledger)} entries, "
            f"{sum(spent_live.values())} tokens spent in Live {run.live})"
        ):
            st.caption(
                "Newest first. Your balance is this list replayed from the most "
                "recent entered figure, so nothing is edited in place."
            )
            for entry in run.recent:
                amounts = ", ".join(
                    f"{gd.TOKEN_SHORT[t]} {v}" for t, v in entry.amounts.items() if v
                )
                mark = "=" if entry.kind == gl.SET else "-"
                st.markdown(
                    f"`Live {entry.live}`  {run.describe(entry)}  "
                    f"<span style='color:#888'>{mark} {amounts or 'nothing'}</span>",
                    unsafe_allow_html=True,
                )

    # --- guide text for this Live ----------------------------------------
    block = conn.execute("SELECT * FROM guide_live WHERE live = ?", (run.live,)).fetchone()
    if block:
        with st.expander(f"Guide advice for Live {run.live}", expanded=False):
            for field, heading in (
                ("song", "Song strategy"),
                ("course", "Course strategy"),
                ("purchase", "Purchase timing"),
            ):
                if block[field]:
                    st.markdown(f"**{heading}**")
                    st.write(block[field])

    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Restart run (keep nothing)", width="stretch"):
            run.reset()
            commit()
    with c2:
        if st.button("Archive and restart", width="stretch"):
            gl.archive(conn, run, "completed" if run.finished else "gave up")
            run.reset()
            commit()
    with c3:
        past = gl.history(conn, limit=1)
        st.caption(f"{len(gl.history(conn, limit=99))} archived runs" if past else "No archived runs yet.")


# --- song value board -----------------------------------------------------

def render_songs(conn: sqlite3.Connection) -> None:
    st.subheader("Song values")
    st.caption(
        "Net value is tracentrial.org's estimate of the attribute points a song "
        "returns for its cost, bought on time. Token costs are GameTora's "
        "in-game numbers."
    )
    threshold = st.slider(
        "Highlight songs worth at least this much", -10.0, 70.0, 20.0, 1.0,
        help="Anything at or above this is marked as a priority buy.",
    )

    rows = []
    for song in gd.SONGS:
        if song.special:
            continue
        rows.append(
            {
                "Live": song.live,
                "Song": song.name,
                "Effect": song.effect,
                **{gd.TOKEN_LABELS[t]: v for t, v in song.cost_map().items()},
                "Total": song.total_cost,
                "Net value": song.net_value,
            }
        )
    frame = pd.DataFrame(rows).sort_values(["Live", "Net value"], ascending=[True, False])

    def highlight(row):
        net = row["Net value"]
        if net is None:
            return [""] * len(row)
        if net >= threshold:
            return ["background-color: rgba(46,125,79,0.16)"] * len(row)
        if net < 0:
            return ["background-color: rgba(193,55,58,0.14)"] * len(row)
        return [""] * len(row)

    st.dataframe(
        frame.style.apply(highlight, axis=1).format({"Net value": "{:+g}"}),
        width="stretch",
        hide_index=True,
        height=640,
    )

    priority = [s for s in gd.SONGS if not s.special and (s.net_value or 0) >= threshold]
    skip = [s for s in gd.SONGS if not s.special and (s.net_value or 0) < 0]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Priority buys ({len(priority)})**")
        for s in priority:
            st.markdown(
                f"- Live {s.live} · **{s.name}** {net_badge(s.net_value)} · {s.total_cost} tokens",
                unsafe_allow_html=True,
            )
    with c2:
        st.markdown(f"**Negative value ({len(skip)})**")
        for s in skip:
            st.markdown(
                f"- Live {s.live} · **{s.name}** {net_badge(s.net_value)}",
                unsafe_allow_html=True,
            )
        st.caption(
            "These are the songs the guide wants you to refresh past. Leaving "
            "them unbought is what keeps the pool clean for later Lives."
        )

    with st.expander("Specials (granted, never bought)"):
        for s in gd.SONGS:
            if s.special:
                st.markdown(f"- **{s.name}** - {s.effect}")
        st.caption(
            f"GIRLS' LEGEND U unlocks by learning at least "
            f"{gd.GIRLS_LEGEND_UNLOCK} songs before early December of the Senior year."
        )


# --- guide reader ---------------------------------------------------------

def render_guide(conn: sqlite3.Connection) -> None:
    sections = list(
        conn.execute(
            "SELECT * FROM guide_section WHERE body != '' ORDER BY position"
        )
    )
    if not sections:
        st.info("No guide text imported yet. Run `python guide_ingest.py`.")
        return
    st.caption("Imported from tracentrial.org. Sections that are card tables rather than prose are not included.")
    labels = {f"{s['kicker']} - {s['title']}": s for s in sections}
    picked = st.radio("Section", list(labels), label_visibility="collapsed")
    section = labels[picked]
    st.subheader(section["title"])
    if section["lead"]:
        st.caption(section["lead"])
    st.write(section["body"])
