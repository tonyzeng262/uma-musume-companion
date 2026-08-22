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
    STYLES,
    Category,
    Weights,
    optimize,
    score_card,
)

st.set_page_config(page_title="Uma Team Trials Builder", page_icon="🐎", layout="wide")

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

    st.divider()
    st.header("Scoring")
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
        "Penalty for repeating a style in a category", 0, 400, 120, 10,
        help="0 ignores running style entirely. 400 makes three distinct "
             "styles per category effectively mandatory.",
    )
    weights = Weights(aptitude=w_apt, tier=w_tier, stats=w_stats, style_penalty=float(style_penalty))

    st.divider()
    dirt_dist = st.multiselect(
        "Distances the Dirt category races",
        options=["sprint", "mile", "medium", "long"],
        default=["mile", "medium"],
        help="Dirt is a surface, not a distance. Its aptitude score averages "
             "whichever distances you expect to see.",
    )

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
    st.subheader("Which umas do you own?")
    global_only = st.checkbox(
        "Only show cards released on Global", value=True,
        help="Cards not yet on Global are in the database but are unrated by the tierlist.",
    )
    cards = roster.all_cards(conn, global_only=global_only)
    by_name = {r["name"]: r["card_id"] for r in cards}
    current = set(roster.owned_ids(conn))
    id_to_name = {r["card_id"]: r["name"] for r in cards}

    picked = st.multiselect(
        "Your umas",
        options=list(by_name),
        default=[id_to_name[c] for c in current if c in id_to_name],
        placeholder="Search and add the umas you have trained",
    )
    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("Save roster", type="primary", width="stretch"):
            roster.set_owned(conn, [by_name[n] for n in picked])
            st.success(f"Saved {len(picked)} umas.")
            st.rerun()
    with col_b:
        st.caption(f"{len(picked)} selected - you need at least 15 to fill a team.")

    with st.expander("Import from screenshots"):
        views_import.render(conn)

    with st.expander("Bulk add by name (one per line)"):
        blob = st.text_area("Names", height=140, label_visibility="collapsed")
        if st.button("Add these"):
            wanted = [ln.strip() for ln in blob.splitlines() if ln.strip()]
            lower = {n.lower(): n for n in by_name}
            found = [by_name[lower[n.lower()]] for n in wanted if n.lower() in lower]
            missing = [n for n in wanted if n.lower() not in lower]
            roster.set_owned(conn, sorted(set(roster.owned_ids(conn)) | set(found)))
            st.success(f"Added {len(found)}.")
            if missing:
                st.warning("Not recognised: " + ", ".join(missing))
            st.rerun()

    owned = roster.owned_ids(conn)
    if owned:
        st.divider()
        st.subheader("Raised aptitudes")
        st.caption(
            "The database holds each card's base aptitudes. If you have raised one "
            "in game through inheritance, override it here so the optimizer uses "
            "your actual grade."
        )
        ov = roster.overrides(conn)
        names = {cid: id_to_name.get(cid) or str(cid) for cid in owned}
        target = st.selectbox("Uma", options=owned, format_func=lambda c: names[c])
        row = roster.card(conn, target)
        cols = st.columns(5)
        for i, apt in enumerate(db.APTITUDES):
            base = row[f"apt_{apt}"] or "G"
            cur = ov.get(target, {}).get(apt, base)
            with cols[i % 5]:
                new = st.selectbox(
                    f"{apt.title()} (base {base})",
                    options=list(GRADES),
                    index=list(GRADES).index(cur) if cur in GRADES else list(GRADES).index(base),
                    key=f"ov_{target}_{apt}",
                )
                if new != cur:
                    roster.set_override(conn, target, apt, None if new == base else new)
                    st.rerun()
        if st.button("Reset this uma to base aptitudes"):
            roster.clear_overrides(conn, target)
            st.rerun()


# --- optimizer ------------------------------------------------------------

with tab_team:
    views = roster.owned_views(conn)
    if not views:
        st.info("Add the umas you own on the **My umas** tab, then come back here.")
    else:
        st.caption(f"Choosing from your {len(views)} umas.")
        result = optimize(views, weights, categories=categories)

        m1, m2, m3 = st.columns(3)
        m1.metric("Team score", f"{result.total_score:,.0f}")
        m2.metric("Slots filled", f"{len(result.assignments)}/15")
        m3.metric("Style clashes", result.style_conflicts)

        if result.unfilled:
            st.warning(
                "Not enough umas to fill: " + ", ".join(result.unfilled)
                + ". Add more on the **My umas** tab."
            )
        if result.style_conflicts:
            st.warning(
                f"{result.style_conflicts} umas share a running style with a teammate in "
                "their category. Raise the style penalty to forbid it, or train an uma "
                "in a different style."
            )

        grouped = result.by_category()
        used = {a.card_id for a in result.assignments}
        for cat in categories:
            group = grouped.get(cat.key, [])
            if not group:
                continue
            st.subheader(f"{cat.label}")
            cols = st.columns(3)
            for col, a in zip(cols, group):
                with col:
                    card_row = roster.card(conn, a.card_id)
                    if card_row and card_row["image_url"]:
                        st.image(card_row["image_url"], width=110)
                    st.markdown(f"**{a.name}** {tier_badge(a.tier)}", unsafe_allow_html=True)
                    flag = " ⚠️" if a.duplicate_style else ""
                    st.markdown(f"Run as **{a.style_label}**{flag}")
                    chips = [grade_chip(cat.surface.title(), a.surface_grade)]
                    chips += [grade_chip(d.title(), g) for d, g in a.distance_grades.items()]
                    chips += [grade_chip(STYLE_LABELS[a.style].split()[0], a.style_grade)]
                    st.markdown("".join(chips), unsafe_allow_html=True)
                    st.caption(
                        f"score {a.score:.0f}  "
                        f"(aptitude {a.breakdown['aptitude']:.0f}, "
                        f"tier {a.breakdown['tier']:.0f}, "
                        f"stats {a.breakdown['stats']:.0f})"
                    )
                    if card_row and card_row["tier_note"]:
                        with st.expander("Tierlist notes"):
                            st.write(card_row["tier_note"])

            bench = sorted(
                (
                    (max(score_card(v, cat, style, weights)[0] for style in STYLES), v)
                    for v in views
                    if v.card_id not in used
                ),
                key=lambda t: -t[0],
            )[:3]
            if bench:
                st.caption(
                    "Next best on the bench: "
                    + ", ".join(f"{v.name} ({s:.0f})" for s, v in bench)
                )
            st.divider()

        if result.assignments:
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
            st.dataframe(table, width="stretch", hide_index=True)
            st.download_button(
                "Download team as CSV",
                table.to_csv(index=False),
                file_name="team_trials_team.csv",
                mime="text/csv",
            )


# --- tierlist browser -----------------------------------------------------

with tab_browse:
    st.subheader("Team Trials tierlist")
    st.caption("Tiers and write-ups from tracentrial.org; aptitudes and stats from GameTora.")
    q = st.text_input("Search", placeholder="Name...")
    tiers = st.multiselect("Tier", ["S", "A", "B", "C", "unrated"], default=["S", "A", "B", "C"])

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
    st.caption(f"{len(rows)} umas")
    for row in rows:
        header = f"{row['tier'] or '-'}  |  {row['name']}"
        with st.expander(header):
            left, right = st.columns([1, 3])
            with left:
                if row["image_url"]:
                    st.image(row["image_url"], width=140)
            with right:
                st.markdown(tier_badge(row["tier"]), unsafe_allow_html=True)
                st.markdown(
                    "".join(grade_chip(a.title(), row[f"apt_{a}"] or "?") for a in db.APTITUDES),
                    unsafe_allow_html=True,
                )
                if row["tier_note"]:
                    st.write(row["tier_note"])
                else:
                    st.caption("No tierlist write-up for this card.")
                growth = ", ".join(
                    f"{s.title()} +{row[f'growth_{s}']}%"
                    for s in db.STATS
                    if row[f"growth_{s}"]
                )
                st.caption(f"Growth: {growth or 'none'}")

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
