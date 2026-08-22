"""First-boot setup, for running somewhere that starts from the repo alone.

`uma.db` is deliberately not committed -- it is derived data, and a binary
blob in git would go stale silently. On a normal machine you build it once
with `python ingest.py`. On a hosted container (Streamlit Community Cloud and
friends) the filesystem starts empty on every deploy, so the app builds it
itself on first load instead.

Rebuilding takes roughly 10-20 seconds and needs outbound HTTPS to
tracentrial.org and gametora.com.
"""

from __future__ import annotations

import os
import sqlite3

import db

# True when the working directory is thrown away between runs, so the app
# should say so rather than let someone build a roster that quietly vanishes.
EPHEMERAL_ENV_HINTS = ("STREAMLIT_RUNTIME_ENV", "STREAMLIT_SERVER_HEADLESS", "DYNO", "K_SERVICE")


def is_hosted() -> bool:
    """Best guess at whether we are running on a throwaway container."""
    if os.environ.get("UMA_HOSTED", "").lower() in {"1", "true", "yes"}:
        return True
    if os.environ.get("UMA_HOSTED", "").lower() in {"0", "false", "no"}:
        return False
    # Streamlit Cloud sets this to "cloud"; a local `streamlit run` does not.
    return os.environ.get("STREAMLIT_RUNTIME_ENV", "").lower() == "cloud"


def needs_reference_data(conn: sqlite3.Connection) -> bool:
    return not db.has_reference_data(conn)


def needs_guide(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT count(*) AS n FROM sqlite_master WHERE type='table' AND name='guide_live'"
    ).fetchone()
    if not row or not row["n"]:
        return True
    return not conn.execute("SELECT count(*) AS n FROM guide_live").fetchone()["n"]


def build_all(log=print) -> dict:
    """Run both importers. Imported lazily so the app starts fast normally."""
    import guide_ingest
    import ingest

    summary = {"cards": ingest.build(log=log)}
    try:
        summary["guide"] = guide_ingest.build(log=log)
    except Exception as exc:  # the guide is a bonus; cards alone are usable
        log(f"! guide import failed: {exc}")
        summary["guide"] = None
    return summary
