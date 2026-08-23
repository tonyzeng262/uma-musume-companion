"""The Grand Live run tracker, song value board, and guide reader.

The run tracker is laid out as a dashboard rather than a page: a status strip
across the top, then three columns that each own one job -- your tokens, the
songs, your courses. Anything long (the song pool, the course log, the guide
text, the ledger) lives in a fixed-height pane or a popover, so the page itself
never grows and nothing you need mid-run is below the fold.
"""

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

# Heights chosen so the whole tracker fits a 1280x800 window without the page
# scrolling; the panes scroll internally instead.
SONG_PANE = 330
SIDE_PANE = 190


# --- small rendering helpers ---------------------------------------------

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


def chip(text: str, bg: str, dim: bool = False) -> str:
    return (
        f"<span style='display:inline-block;margin:1px 3px 1px 0;padding:1px 7px;"
        f"border-radius:9px;background:{bg};color:#fff;font-size:11px;"
        f"white-space:nowrap;opacity:{0.35 if dim else 1}'>{text}</span>"
    )


def cost_chips(cost: dict[str, int]) -> str:
    parts = [
        chip(f"{gd.TOKEN_SHORT[t]} {v}", TOKEN_COLOR[t]) for t, v in cost.items() if v
    ]
    return "".join(parts) or "<span style='color:#888;font-size:11px'>free</span>"


def token_chips(values: dict[str, int]) -> str:
    """All five tokens on one line, negatives flipped to red."""
    return "".join(
        chip(
            f"{gd.TOKEN_SHORT[t]} {values[t]}",
            NEGATIVE if values[t] < 0 else TOKEN_COLOR[t],
            dim=values[t] == 0,
        )
        for t in gd.TOKENS
    )


def net_badge(net: float | None) -> str:
    if net is None:
        return ""
    sign = "+" if net > 0 else ""
    return (
        f"<span style='display:inline-block;padding:1px 8px;border-radius:4px;"
        f"background:{value_color(net)};color:#fff;font-weight:700;font-size:11px'>"
        f"{sign}{net:g}</span>"
    )


def stat(label: str, value, color: str | None = None, note: str = "") -> None:
    st.markdown(
        f"<div style='line-height:1.2'>"
        f"<span style='font-size:10px;color:#888;text-transform:uppercase;"
        f"letter-spacing:.09em'>{label}</span><br>"
        f"<span style='font-size:20px;font-weight:600;color:{color or 'inherit'}'>{value}</span>"
        f"<span style='font-size:11px;color:#888'> {note}</span></div>",
        unsafe_allow_html=True,
    )


def token_inputs(
    defaults: dict[str, int] | None = None,
    key_prefix: str = "tok",
    max_value: int = 9999,
) -> dict[str, int]:
    """Five side-by-side token boxes, colour-labelled Da / Pa / Vo / Vi / Co.

    A course usually costs one or two token types, so these default to zero and
    you only touch the ones you actually spent -- no filling in the blanks.
    """
    values: dict[str, int] = {}
    cols = st.columns(5, gap="small")
    for col, token in zip(cols, gd.TOKENS):
        with col:
            st.markdown(
                f"<div style='text-align:center;font-size:11px;font-weight:700;"
                f"color:{TOKEN_COLOR[token]};line-height:1'>{gd.TOKEN_SHORT[token]}</div>",
                unsafe_allow_html=True,
            )
            values[token] = st.number_input(
                gd.TOKEN_LABELS[token],
                min_value=0,
                max_value=max_value,
                value=int((defaults or {}).get(token, 0)),
                step=1,
                key=f"{key_prefix}_{token}",
                label_visibility="collapsed",
            )
    return values


# --- the run tracker ------------------------------------------------------

def render_run(conn: sqlite3.Connection) -> None:
    run = gl.load(conn)

    def commit() -> None:
        gl.save(conn, run)
        st.rerun()

    _header(conn, run, commit)
    left, mid, right = st.columns([1.05, 1.55, 1.05], gap="medium")
    with left:
        _tokens_panel(run, commit)
    with mid:
        _songs_panel(run, commit)
    with right:
        _courses_panel(run, commit)


def _header(conn: sqlite3.Connection, run: gl.RunState, commit) -> None:
    cols = st.columns([2.5, 1.15, 1.15, 1.3, 2.1], vertical_alignment="center")

    with cols[0]:
        picked = st.segmented_control(
            "Live",
            options=list(gd.LIVE_LABELS),
            default=run.live,
            format_func=lambda n: f"Live {n}",
            key="live_pick",
            label_visibility="collapsed",
        )
        if picked and picked != run.live:
            run.goto_live(picked)
            commit()
        block = conn.execute(
            "SELECT refresh FROM guide_live WHERE live = ?", (run.live,)
        ).fetchone()
        if block and block["refresh"]:
            st.caption(f"Refresh pattern {block['refresh']}")

    with cols[1]:
        target = gd.GRAND_SUCCESS_SONGS
        stat(
            "Songs",
            f"{run.songs_learned}",
            POSITIVE if run.on_track_for_grand_success else None,
            f"/ {target}",
        )
    with cols[2]:
        spare = run.spare_total() if run.has_baseline else 0
        short = any(run.shortfall().values())
        stat("Spare", spare if run.has_baseline else "--",
             NEGATIVE if short else POSITIVE if run.has_baseline else None)
    with cols[3]:
        if run.finished:
            stat("Status", "Done", POSITIVE)
        else:
            stat("Courses", run.courses_this_live, note="this Live")

    with cols[4]:
        b = st.columns(3)
        with b[0]:
            label = "Finish" if run.live >= gl.LAST_LIVE else "End Live"
            if st.button(label, type="primary", width="stretch"):
                run.advance_live()
                commit()
        with b[1]:
            if st.button("Undo", width="stretch", disabled=not run.can_undo):
                run.undo()
                commit()
        with b[2]:
            with st.popover("More", width="stretch"):
                _guide_text(conn, run)
                st.divider()
                _ledger(run)
                st.divider()
                if st.button("Restart run", width="stretch"):
                    run.reset()
                    commit()
                if st.button("Archive and restart", width="stretch"):
                    gl.archive(conn, run, "completed" if run.finished else "gave up")
                    run.reset()
                    commit()
                past = gl.history(conn, limit=99)
                st.caption(f"{len(past)} archived runs" if past else "No archived runs yet.")


def _tokens_panel(run: gl.RunState, commit) -> None:
    st.markdown("###### Your tokens")
    # The key stamp makes the boxes re-read the saved baseline after any action;
    # otherwise Streamlit keeps showing whatever was typed last.
    stamp = len(run.ledger)
    with st.form("tokens", border=False):
        entered = token_inputs(run.entered if run.has_baseline else None, f"tok{stamp}")
        if st.form_submit_button("Save as my balance", type="primary", width="stretch"):
            run.set_tokens(entered)
            commit()

    if not run.has_baseline:
        st.caption("Type the five numbers off your lesson screen to start tracking.")
        st.markdown("**Need for songs**")
        st.markdown(token_chips(run.guide_requirement()), unsafe_allow_html=True)
        return

    spare = run.spare()
    hint = gd.COURSE_COST_HINT.get(run.live, 16)
    total = run.spare_total()
    st.markdown("**Spare for courses**")
    st.markdown(token_chips(spare), unsafe_allow_html=True)
    st.caption(f"{total} spare - about {total // hint} courses at ~{hint} each")

    short = run.shortfall()
    if any(short.values()):
        st.markdown(
            f"<span style='color:{NEGATIVE};font-size:12px'>Short "
            + ", ".join(f"{v} {gd.TOKEN_SHORT[t]}" for t, v in short.items() if v)
            + " - spending these costs you a song.</span>",
            unsafe_allow_html=True,
        )

    with st.expander("Balance and spending"):
        st.caption("Have now")
        st.markdown(token_chips(run.tokens), unsafe_allow_html=True)
        st.caption("Need for the songs still worth buying")
        st.markdown(token_chips(run.guide_requirement()), unsafe_allow_html=True)
        spent = run.spent_since_entry
        st.caption(f"Spent since you entered it ({sum(spent.values())})")
        st.markdown(token_chips(spent), unsafe_allow_html=True)
        quoted = gd.TRACENTRIAL_REQUIREMENTS.get(run.live)
        if quoted:
            st.caption("Guide quotes " + " / ".join(str(v) for v in quoted) + " at full pool.")
    if run.overspent():
        st.error("A token has gone negative - re-enter your balance or undo.", icon="!")


def _songs_panel(run: gl.RunState, commit) -> None:
    head = st.columns([2, 1.5], vertical_alignment="center")
    with head[0]:
        st.markdown("###### Songs you can buy")
    with head[1]:
        show_all = st.toggle("Show all", value=False, key="song_all",
                             help="Off shows only songs the guide rates positively.")

    pool = sorted(run.pool(), key=lambda s: -(s.net_value or 0))
    shown = pool if show_all else [s for s in pool if (s.net_value or 0) > 0]
    skipped = len(pool) - len(shown)

    with st.container(height=SONG_PANE, border=True):
        if not shown:
            st.caption("Nothing left worth buying in this pool.")
        for song in shown:
            row = st.columns([3.4, 2.3, 0.8, 1.15], vertical_alignment="center")
            with row[0]:
                st.markdown(
                    f"<span style='font-size:13px;font-weight:600'>{song.name}</span><br>"
                    f"<span style='font-size:10px;color:#888'>L{song.live} · {song.effect}</span>",
                    unsafe_allow_html=True,
                )
            with row[1]:
                st.markdown(cost_chips(song.cost_map()), unsafe_allow_html=True)
            with row[2]:
                st.markdown(net_badge(song.net_value), unsafe_allow_html=True)
            with row[3]:
                afford = run.can_afford(song.key) if run.has_baseline else True
                if st.button(
                    "Buy" if afford else "Buy!",
                    key=f"buy_{song.key}",
                    width="stretch",
                    type="primary" if afford and (song.net_value or 0) > 20 else "secondary",
                    help=None if afford else "You cannot afford this yet.",
                ):
                    run.buy_song(song.key)
                    commit()

    foot = st.columns([2, 1.4], vertical_alignment="center")
    with foot[0]:
        if skipped:
            st.caption(f"{skipped} negative-value songs hidden - leave them to keep the pool clean.")
        else:
            st.caption(f"{len(pool)} songs in the pool.")
    with foot[1]:
        with st.popover(f"Bought ({len(run.bought)})", width="stretch"):
            if not run.bought:
                st.caption("Nothing bought yet this run.")
            for key in run.bought:
                song = gd.BY_KEY[key]
                c = st.columns([3, 1])
                c[0].markdown(
                    f"<span style='font-size:12px'>{song.name}</span> {net_badge(song.net_value)}",
                    unsafe_allow_html=True,
                )
                if c[1].button("Refund", key=f"unbuy_{key}", width="stretch"):
                    run.unbuy_song(key)
                    commit()


def _courses_panel(run: gl.RunState, commit) -> None:
    st.markdown("###### Live Technique courses")
    hint = gd.COURSE_COST_HINT.get(run.live, 16)
    with st.form("course", clear_on_submit=True, border=False):
        spend = token_inputs(key_prefix="course", max_value=999)
        note = st.text_input("Note", placeholder="optional note", label_visibility="collapsed")
        if st.form_submit_button("Log this course", width="stretch"):
            if sum(spend.values()) == 0:
                st.warning("Fill in what the course cost first.")
            else:
                run.take_course(spend, note)
                commit()

    taken = [c for c in run.courses if c.live == run.live]
    spent_live = run.spent_in_live(run.live)
    st.caption(
        f"{len(taken)} this Live · {sum(spent_live.values())} tokens spent · "
        f"guide expects ~{hint} per course"
    )
    with st.container(height=SIDE_PANE, border=True):
        if not taken:
            st.caption("No courses logged in this Live yet.")
        for i, course in enumerate(taken):
            c = st.columns([3, 1], vertical_alignment="center")
            with c[0]:
                st.markdown(cost_chips(course.amounts), unsafe_allow_html=True)
                if course.label:
                    st.caption(course.label)
            with c[1]:
                if st.button("x", key=f"delcourse_{i}", width="stretch", help="Remove"):
                    run.drop_course(run.courses.index(course))
                    commit()


def _guide_text(conn: sqlite3.Connection, run: gl.RunState) -> None:
    block = conn.execute("SELECT * FROM guide_live WHERE live = ?", (run.live,)).fetchone()
    st.markdown(f"**Guide advice for Live {run.live}**")
    if not block:
        st.caption("No guide text imported.")
        return
    for field, heading in (
        ("song", "Song strategy"),
        ("course", "Course strategy"),
        ("purchase", "Purchase timing"),
    ):
        if block[field]:
            st.caption(heading)
            st.markdown(f"<span style='font-size:13px'>{block[field]}</span>",
                        unsafe_allow_html=True)


def _ledger(run: gl.RunState) -> None:
    st.markdown(f"**Everything logged ({len(run.ledger)})**")
    if not run.ledger:
        st.caption("Nothing yet.")
        return
    with st.container(height=200, border=False):
        for entry in run.recent:
            amounts = ", ".join(
                f"{gd.TOKEN_SHORT[t]} {v}" for t, v in entry.amounts.items() if v
            )
            mark = "=" if entry.kind == gl.SET else "-"
            st.markdown(
                f"<span style='font-size:12px'>`L{entry.live}` {run.describe(entry)} "
                f"<span style='color:#888'>{mark} {amounts or 'nothing'}</span></span>",
                unsafe_allow_html=True,
            )


# --- song value board -----------------------------------------------------

def render_songs(conn: sqlite3.Connection) -> None:
    head = st.columns([3, 2], vertical_alignment="center")
    with head[0]:
        st.markdown("###### Song values")
        st.caption(
            "Net value is tracentrial's estimate of the attribute points a song "
            "returns for its cost. Token costs are GameTora's in-game numbers."
        )
    with head[1]:
        threshold = st.slider("Priority at or above", -10.0, 70.0, 20.0, 1.0)

    rows = [
        {
            "Live": s.live,
            "Song": s.name,
            "Effect": s.effect,
            **{gd.TOKEN_SHORT[t]: v for t, v in s.cost_map().items()},
            "Total": s.total_cost,
            "Net": s.net_value,
        }
        for s in gd.SONGS
        if not s.special
    ]
    frame = pd.DataFrame(rows).sort_values(["Live", "Net"], ascending=[True, False])

    def highlight(row):
        net = row["Net"]
        if net is None:
            return [""] * len(row)
        if net >= threshold:
            return ["background-color: rgba(46,125,79,0.16)"] * len(row)
        if net < 0:
            return ["background-color: rgba(193,55,58,0.14)"] * len(row)
        return [""] * len(row)

    body = st.columns([3, 1.5], gap="medium")
    with body[0]:
        st.dataframe(
            frame.style.apply(highlight, axis=1).format({"Net": "{:+g}"}),
            width="stretch",
            hide_index=True,
            height=430,
        )
    with body[1]:
        priority = [s for s in gd.SONGS if not s.special and (s.net_value or 0) >= threshold]
        skip = [s for s in gd.SONGS if not s.special and (s.net_value or 0) < 0]
        with st.container(height=430, border=True):
            st.markdown(f"**Priority buys ({len(priority)})**")
            for s in priority:
                st.markdown(
                    f"<span style='font-size:12px'>L{s.live} {s.name}</span> "
                    f"{net_badge(s.net_value)}",
                    unsafe_allow_html=True,
                )
            st.markdown(f"**Leave these ({len(skip)})**")
            for s in skip:
                st.markdown(
                    f"<span style='font-size:12px'>L{s.live} {s.name}</span> "
                    f"{net_badge(s.net_value)}",
                    unsafe_allow_html=True,
                )
            st.caption(
                "Refreshing past the negatives is what keeps the pool clean for "
                "later Lives."
            )

    with st.expander("Specials (granted, never bought)"):
        for s in gd.SONGS:
            if s.special:
                st.markdown(f"- **{s.name}** - {s.effect}")
        st.caption(
            f"GIRLS' LEGEND U unlocks by learning at least {gd.GIRLS_LEGEND_UNLOCK} "
            "songs before early December of the Senior year."
        )


# --- guide reader ---------------------------------------------------------

def render_guide(conn: sqlite3.Connection) -> None:
    sections = list(
        conn.execute("SELECT * FROM guide_section WHERE body != '' ORDER BY position")
    )
    if not sections:
        st.info("No guide text imported yet. Run `python guide_ingest.py`.")
        return
    labels = {f"{s['kicker']} - {s['title']}": s for s in sections}
    picked = st.selectbox("Section", list(labels), label_visibility="collapsed")
    section = labels[picked]
    st.markdown(f"###### {section['title']}")
    if section["lead"]:
        st.caption(section["lead"])
    with st.container(height=430, border=True):
        st.write(section["body"])
    st.caption(
        "Imported from tracentrial.org. Sections that are card tables rather "
        "than prose are not included."
    )
