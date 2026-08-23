"""Uma Musume Team Trials builder.

Run:  python -m streamlit run app.py
"""

from __future__ import annotations


import pandas as pd
import streamlit as st

import bootstrap
import db
import roster
import views_grandlive
import views_import
from optimizer import (
    DEFAULT_CATEGORIES,
    GRADES,
    STYLE_LABELS,
    STYLE_SHORT,
    STYLES,
    Category,
    Weights,
    optimize,
    score_card,
)

st.set_page_config(
    page_title="Uma Team Trials Builder",
    page_icon="🐎",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Streamlit's stock padding costs about 150px of vertical space, which is the
# difference between this fitting on a laptop screen and not. Tighten the
# chrome so each tab is a dashboard rather than a scroll.
st.markdown(
    """
    <style>
      [data-testid="stMain"] .block-container {padding: 1.6rem 1.4rem 1.2rem;}
      [data-testid="stHeader"] {height: 2.2rem;}
      [data-testid="stMain"] [data-testid="stVerticalBlock"] {gap: 0.55rem;}
      [data-testid="stTabs"] [data-baseweb="tab"] {padding-top: 0.2rem; padding-bottom: 0.2rem;}
      h1 {font-size: 1.35rem !important; padding: 0 0 0.2rem !important;}
      h6 {margin-bottom: 0.1rem !important; padding-bottom: 0 !important;}
      /* A long roster would otherwise push everything below the fold. */
      [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:first-child
        {max-height: 118px; overflow-y: auto;}
      [data-testid="stElementToolbar"] {display: none;}
      /* Five token boxes have to sit side by side in a third of the width, so
         drop the +/- steppers: these are typed, never nudged. */
      [data-testid="stNumberInputStepUp"],
      [data-testid="stNumberInputStepDown"] {display: none !important;}
      [data-testid="stNumberInput"] label {
        font-size: 11px !important; margin-bottom: 0 !important; min-height: 0 !important;}
      [data-testid="stNumberInput"] input {padding: 0.25rem 0.4rem !important; text-align: center;}
    </style>
    """,
    unsafe_allow_html=True,
)

TIER_COLOR = {"S": "#e0457b", "A": "#ef7d2f", "B": "#e5b53a", "C": "#5aab6b"}
GRADE_COLOR = {
    "S": "#e0457b", "A": "#ef7d2f", "B": "#e5b53a", "C": "#5aab6b",
    "D": "#4a9ad4", "E": "#7a6fd0", "F": "#8a8a8a", "G": "#8a8a8a",
}


def get_conn():
    # Deliberately not cached: Streamlit reruns can land on a different thread
    # than the one that created a connection, which SQLite refuses. Opening a
    # fresh connection per run costs microseconds and sidesteps it entirely.
    return db.connect()


def grade_chip(label: str, grade: str) -> str:
    color = GRADE_COLOR.get(grade, "#8a8a8a")
    return (
        f"<span style='display:inline-block;margin:1px 3px 1px 0;padding:1px 7px;"
        f"border-radius:9px;background:{color};color:#fff;font-size:11px;"
        f"white-space:nowrap'>{label} {grade}</span>"
    )


def tier_badge(tier: str | None) -> str:
    if not tier:
        return "<span style='color:#888;font-size:12px'>unrated</span>"
    return (
        f"<span style='display:inline-block;padding:1px 9px;border-radius:4px;"
        f"background:{TIER_COLOR.get(tier, '#888')};color:#fff;font-weight:700;"
        f"font-size:12px'>{tier}</span>"
    )


# --- sidebar --------------------------------------------------------------

conn = get_conn()

with st.sidebar:
    st.header("Data")
    if bootstrap.needs_reference_data(conn) or bootstrap.needs_guide(conn):
        # A hosted container starts from the repo alone, with no uma.db. Build
        # it rather than showing an error nobody can act on from a browser.
        lines: list[str] = []
        status = st.status("Building the card database (10-20s)...", expanded=True)
        try:
            with status:
                bootstrap.build_all(log=lambda m: (lines.append(str(m)), st.write(str(m)))[0])
            status.update(label="Database ready.", state="complete", expanded=False)
        except Exception as exc:
            status.update(label="Could not build the database.", state="error")
            st.error(
                f"{exc}\n\nThe app needs outbound access to tracentrial.org and "
                "gametora.com. Locally you can run `python ingest.py` instead."
            )
            st.stop()
        conn = get_conn()
        st.rerun()

    counts = conn.execute(
        "SELECT count(*) AS cards, sum(released_global) AS glob,"
        " sum(tier IS NOT NULL) AS tiered FROM uma_cards"
    ).fetchone()
    last = conn.execute("SELECT max(ran_at) AS t FROM ingest_log").fetchone()["t"]
    st.caption(
        f"{counts['cards']} cards - {counts['glob']} on Global - "
        f"{counts['tiered']} tier-rated\nlast refreshed {last or 'unknown'}"
    )
    if st.button("Refresh from tracentrial + GameTora", width="stretch"):
        with st.spinner("Fetching..."):
            try:
                bootstrap.build_all(log=lambda m: None)
                st.success("Refreshed. Your roster and run were not touched.")
            except Exception as exc:
                st.error(f"Refresh failed: {exc}")
        st.rerun()

    if bootstrap.is_hosted():
        st.warning(
            "**Hosted copy.** This container's disk is wiped whenever the app "
            "sleeps or redeploys, so your roster and any Grand Live run in "
            "progress will reset. Card data rebuilds itself automatically.",
            icon="⚠️",
        )

    # Collapsed by default: these are tuning knobs, not day-to-day controls.
    with st.expander("Team scoring"):
        w_apt = st.slider(
            "Aptitude fit", 0.0, 2.0, 1.0, 0.05,
            help="Surface x distance x running-style aptitude. The dominant term: "
                 "an uma who cannot run the race is worthless regardless of tier.",
        )
        w_tier = st.slider(
            "Tierlist rating", 0.0, 2.0, 0.5, 0.05,
            help="How much the tracentrial.org Team Trials tier (S/A/B/C) counts. "
                 "Unrated cards score as a neutral 40.",
        )
        w_stats = st.slider(
            "Stat caps and growth", 0.0, 1.0, 0.15, 0.05,
            help="Five-star stat caps and growth bonuses, weighted for the distance.",
        )
        style_penalty = st.slider(
            "Penalty for repeating a style", 0, 400, 120, 10,
            help="0 ignores running style entirely. 400 makes three distinct "
                 "styles per category effectively mandatory.",
        )
        dirt_dist = st.multiselect(
            "Distances the Dirt category races",
            options=["sprint", "mile", "medium", "long"],
            default=["mile", "medium"],
            help="Dirt is a surface, not a distance. Its aptitude score averages "
                 "whichever distances you expect to see.",
        )
    weights = Weights(aptitude=w_apt, tier=w_tier, stats=w_stats, style_penalty=float(style_penalty))

categories = tuple(
    Category(c.key, c.label, c.surface, tuple(dirt_dist) or ("mile",), c.stat_weights)
    if c.key == "dirt" else c
    for c in DEFAULT_CATEGORIES
)

st.title("Uma Musume companion")

tab_team, tab_roster, tab_browse, tab_run, tab_songs, tab_guide = st.tabs(
    ["Optimize", "My umas", "Tierlist", "Grand Live run", "Song values", "Guide"]
)

with tab_run:
    views_grandlive.render_run(conn)

with tab_songs:
    views_grandlive.render_songs(conn)

with tab_guide:
    views_grandlive.render_guide(conn)


# --- roster ---------------------------------------------------------------

with tab_roster:
    bar = st.columns([1.3, 1.2, 1.2, 1.2, 2.6], vertical_alignment="center")
    with bar[0]:
        global_only = st.toggle(
            "Global only", value=True,
            help="Cards not yet on Global are in the database but are unrated.",
        )
    cards = roster.all_cards(conn, global_only=global_only)
    by_name = {r["name"]: r["card_id"] for r in cards}
    current = set(roster.owned_ids(conn))
    id_to_name = {r["card_id"]: r["name"] for r in cards}
    owned = roster.owned_ids(conn)

    with bar[1]:
        with st.popover("Screenshots", width="stretch"):
            views_import.render(conn)
    with bar[2]:
        with st.popover("Paste names", width="stretch"):
            blob = st.text_area("Names", height=160, label_visibility="collapsed",
                                placeholder="One uma per line")
            if st.button("Add these", width="stretch"):
                wanted = [ln.strip() for ln in blob.splitlines() if ln.strip()]
                lower = {n.lower(): n for n in by_name}
                found = [by_name[lower[n.lower()]] for n in wanted if n.lower() in lower]
                missing = [n for n in wanted if n.lower() not in lower]
                roster.set_owned(conn, sorted(set(roster.owned_ids(conn)) | set(found)))
                st.success(f"Added {len(found)}.")
                if missing:
                    st.warning("Not recognised: " + ", ".join(missing))
                st.rerun()
    with bar[3]:
        # A disabled popover still runs its body, so the empty-roster case has
        # to be handled in here rather than left to `disabled`. With no options
        # st.selectbox returns None, and roster.card(None) is None.
        with st.popover("Aptitudes", width="stretch", disabled=not owned):
            target = row = None
            if not owned:
                st.caption("Add some umas to your roster first.")
            else:
                st.caption(
                    "The database holds base aptitudes. Raised one through "
                    "inheritance? Override it so the optimizer uses your grade."
                )
                names = {cid: id_to_name.get(cid) or str(cid) for cid in owned}
                target = st.selectbox(
                    "Uma", options=owned, format_func=lambda c: names.get(c, str(c))
                )
                row = roster.card(conn, target) if target is not None else None
                if row is None:
                    st.warning(
                        "That uma is not in the card database any more - "
                        "re-save your roster to clear it."
                    )

            if row is not None:
                ov = roster.overrides(conn)
                grid = st.columns(5)
                for i, apt in enumerate(db.APTITUDES):
                    base = row[f"apt_{apt}"] or "G"
                    cur = ov.get(target, {}).get(apt, base)
                    with grid[i % 5]:
                        new = st.selectbox(
                            f"{apt.title()} ({base})",
                            options=list(GRADES),
                            index=list(GRADES).index(cur) if cur in GRADES
                            else list(GRADES).index(base),
                            key=f"ov_{target}_{apt}",
                        )
                        if new != cur:
                            roster.set_override(conn, target, apt, None if new == base else new)
                            st.rerun()
                if st.button("Reset to base aptitudes", width="stretch"):
                    roster.clear_overrides(conn, target)
                    st.rerun()

    picked = st.multiselect(
        "Your umas",
        options=list(by_name),
        default=[id_to_name[c] for c in current if c in id_to_name],
        placeholder="Search and add the umas you have trained",
        label_visibility="collapsed",
    )
    with bar[4]:
        save = st.columns([1, 2], vertical_alignment="center")
        with save[0]:
            if st.button("Save roster", type="primary", width="stretch"):
                roster.set_owned(conn, [by_name[n] for n in picked])
                st.rerun()
        with save[1]:
            short_by = max(0, 15 - len(picked))
            st.caption(
                f"{len(picked)} selected"
                + (f" - {short_by} more needed for a full team" if short_by else " - enough for a full team")
            )


# --- optimizer ------------------------------------------------------------

with tab_team:
    views = roster.owned_views(conn)
    if not views:
        st.info("Add the umas you own on the **My umas** tab, then come back here.")
    else:
        result = optimize(views, weights, categories=categories)
        grouped = result.by_category()
        used = {a.card_id for a in result.assignments}

        # Status strip: the whole team's health in one line.
        head = st.columns([1.1, 1.1, 1.1, 1.2, 2.5], vertical_alignment="center")
        head[0].markdown(
            f"<span style='font-size:10px;color:#888;text-transform:uppercase'>Team score</span>"
            f"<br><span style='font-size:20px;font-weight:600'>{result.total_score:,.0f}</span>",
            unsafe_allow_html=True,
        )
        head[1].markdown(
            f"<span style='font-size:10px;color:#888;text-transform:uppercase'>Slots</span>"
            f"<br><span style='font-size:20px;font-weight:600'>{len(result.assignments)}/15</span>",
            unsafe_allow_html=True,
        )
        clash_color = "#c1373a" if result.style_conflicts else "#2e7d4f"
        head[2].markdown(
            f"<span style='font-size:10px;color:#888;text-transform:uppercase'>Clashes</span>"
            f"<br><span style='font-size:20px;font-weight:600;color:{clash_color}'>"
            f"{result.style_conflicts}</span>",
            unsafe_allow_html=True,
        )
        head[3].markdown(
            f"<span style='font-size:10px;color:#888;text-transform:uppercase'>Pool</span>"
            f"<br><span style='font-size:20px;font-weight:600'>{len(views)}</span>"
            f"<span style='font-size:11px;color:#888'> owned</span>",
            unsafe_allow_html=True,
        )
        with head[4]:
            bar = st.columns(2)
            with bar[0]:
                show_art = st.toggle("Portraits", value=False, key="team_art")
            with bar[1]:
                table = pd.DataFrame(
                    [
                        {
                            "Category": a.category_label,
                            "Uma": a.name,
                            "Style": a.style_label,
                            "Tier": a.tier or "-",
                            "Surface": a.surface_grade,
                            "Distance": "/".join(a.distance_grades.values()),
                            "Style apt": a.style_grade,
                            "Score": round(a.score, 1),
                        }
                        for a in result.assignments
                    ]
                )
                st.download_button(
                    "Export CSV",
                    table.to_csv(index=False) if not table.empty else "",
                    file_name="team_trials_team.csv",
                    mime="text/csv",
                    width="stretch",
                    disabled=table.empty,
                )

        if result.unfilled:
            st.warning(
                "Not enough umas to fill: " + ", ".join(result.unfilled)
                + ". Add more on the **My umas** tab."
            )
        if result.style_conflicts:
            st.warning(
                f"{result.style_conflicts} umas share a running style with a teammate in "
                "their category. Raise the style penalty in the sidebar to forbid it."
            )

        # One column per race category, so the whole 15 fits on a screen.
        board = st.columns(len(categories), gap="small")
        for col, cat in zip(board, categories):
            group = grouped.get(cat.key, [])
            with col:
                st.markdown(f"###### {cat.label}")
                with st.container(height=430 if show_art else 300, border=True):
                    if not group:
                        st.caption("Nothing assigned.")
                    for a in group:
                        card_row = roster.card(conn, a.card_id)
                        if show_art and card_row and card_row["image_url"]:
                            st.image(card_row["image_url"], width=72)
                        flag = " ⚠" if a.duplicate_style else ""
                        st.markdown(
                            f"<span style='font-size:13px;font-weight:600'>{a.name}</span> "
                            f"{tier_badge(a.tier)}<br>"
                            f"<span style='font-size:11px;color:#888'>"
                            f"{a.style_label}{flag} · {a.score:.0f}</span>",
                            unsafe_allow_html=True,
                        )
                        chips = [grade_chip(cat.surface.title(), a.surface_grade)]
                        chips += [grade_chip(d.title(), g) for d, g in a.distance_grades.items()]
                        chips += [grade_chip(STYLE_SHORT[a.style], a.style_grade)]
                        st.markdown("".join(chips), unsafe_allow_html=True)
                        st.markdown(
                            "<div style='border-bottom:1px solid rgba(128,128,128,.2);"
                            "margin:6px 0'></div>",
                            unsafe_allow_html=True,
                        )

                bench = sorted(
                    (
                        (max(score_card(v, cat, style, weights)[0] for style in STYLES), v)
                        for v in views
                        if v.card_id not in used
                    ),
                    key=lambda t: -t[0],
                )[:3]
                with st.popover("Bench / notes", width="stretch"):
                    st.caption("Next best not already used elsewhere")
                    for s, v in bench:
                        st.markdown(
                            f"<span style='font-size:12px'>{v.name}</span> "
                            f"<span style='color:#888;font-size:12px'>({s:.0f})</span>",
                            unsafe_allow_html=True,
                        )
                    st.divider()
                    for a in group:
                        card_row = roster.card(conn, a.card_id)
                        if card_row and card_row["tier_note"]:
                            st.markdown(f"**{a.name}**")
                            st.caption(card_row["tier_note"])

        with st.expander("Full team table"):
            if result.assignments:
                st.dataframe(table, width="stretch", hide_index=True)


# --- tierlist browser -----------------------------------------------------

with tab_browse:
    filt = st.columns([2, 3], vertical_alignment="center")
    with filt[0]:
        q = st.text_input("Search", placeholder="Name...", label_visibility="collapsed")
    with filt[1]:
        tiers = st.pills(
            "Tier", ["S", "A", "B", "C", "unrated"], default=["S", "A", "B", "C"],
            selection_mode="multi", label_visibility="collapsed",
        )
    tiers = list(tiers or [])

    sql = "SELECT * FROM uma_cards WHERE released_global = 1"
    params: list = []
    if q:
        sql += " AND name LIKE ?"
        params.append(f"%{q}%")
    clauses = []
    if [t for t in tiers if t != "unrated"]:
        marks = ",".join("?" * len([t for t in tiers if t != "unrated"]))
        clauses.append(f"tier IN ({marks})")
        params += [t for t in tiers if t != "unrated"]
    if "unrated" in tiers:
        clauses.append("tier IS NULL")
    if clauses:
        sql += " AND (" + " OR ".join(clauses) + ")"
    sql += " ORDER BY CASE tier WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2"
    sql += " WHEN 'C' THEN 3 ELSE 4 END, name"

    rows = list(conn.execute(sql, params))

    # Master/detail rather than 96 stacked expanders: pick on the left, read on
    # the right, nothing below the fold.
    listing, detail = st.columns([1.15, 2.4], gap="medium")
    with listing:
        st.caption(f"{len(rows)} umas - click one")
        picker = pd.DataFrame(
            [{"Tier": r["tier"] or "-", "Uma": r["name"]} for r in rows]
        )
        chosen = st.dataframe(
            picker,
            width="stretch",
            hide_index=True,
            height=560,
            on_select="rerun",
            selection_mode="single-row",
            key="tier_pick",
        )
        picked_rows = chosen.selection.rows if chosen and chosen.selection else []

    with detail:
        if not rows:
            st.info("No umas match that filter.")
        else:
            # The selection survives a rerun, so a narrowed filter can leave an
            # index pointing past the end of the shorter list.
            index = picked_rows[0] if picked_rows else 0
            row = rows[index] if 0 <= index < len(rows) else rows[0]
            top = st.columns([1, 3], vertical_alignment="center")
            with top[0]:
                if row["image_url"]:
                    st.image(row["image_url"], width=120)
            with top[1]:
                st.markdown(
                    f"### {row['name']} {tier_badge(row['tier'])}", unsafe_allow_html=True
                )
                st.markdown(
                    "".join(
                        grade_chip(a.title(), row[f"apt_{a}"] or "?") for a in db.APTITUDES
                    ),
                    unsafe_allow_html=True,
                )
                growth = ", ".join(
                    f"{s.title()} +{row[f'growth_{s}']}%"
                    for s in db.STATS
                    if row[f"growth_{s}"]
                )
                st.caption(f"Growth: {growth or 'none'}")

            with st.container(height=390, border=True):
                if row["tier_note"]:
                    st.write(row["tier_note"])
                else:
                    st.caption("No tierlist write-up for this card.")
                skills = roster.skills_for(conn, row["card_id"])
                for kind in ("unique", "innate", "potential", "awakening", "event"):
                    items = skills.get(kind) or []
                    if not items:
                        continue
                    st.markdown(f"**{kind.title()} skills**")
                    for s in items:
                        name = s["name"] or f"(skill {s['skill_id']})"
                        desc = s["description"] or ""
                        st.markdown(f"- **{name}** - {desc}" if desc else f"- **{name}**")
