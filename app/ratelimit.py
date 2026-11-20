"""Persistent SQLite sliding-window hourly allowance (default: 15 scans / hour per visitor).

Demo version only: the download version is unlimited, so every function here is a no-op when
DEMO_MODE is off. A scan is recorded only after it succeeds (`record`); `check` just refuses
when the visitor has no scans left. State lives in SQLite so it survives restarts.
"""
import time
from typing import Dict, Optional

from . import config, db
from .errors import ApiError


def _snapshot(conn, owner: str, now: float) -> Dict[str, float]:
    cutoff = now - config.RATE_WINDOW_SECONDS
    row = conn.execute(
        "SELECT COUNT(*) AS n, MIN(ts) AS oldest FROM rate_limit WHERE ip=? AND ts>?", (owner, cutoff)
    ).fetchone()
    used = int(row["n"])
    reset_in = int(max(0, row["oldest"] + config.RATE_WINDOW_SECONDS - now)) + 1 if used else 0
    limit = config.RATE_LIMIT_PER_HOUR
    return {
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "reset_in_seconds": reset_in,
        "window_seconds": config.RATE_WINDOW_SECONDS,
    }


def status(owner: str) -> Optional[Dict[str, float]]:
    """Current allowance, or None in the download version (no limit)."""
    if not config.DEMO_MODE:
        return None
    with db.connect() as conn:
        return _snapshot(conn, owner, time.time())


def check(owner: str) -> None:
    """Raise 429 when this visitor has used the hourly allowance. Consumes nothing."""
    snap = status(owner)
    if snap and snap["remaining"] <= 0:
        mins = max(1, round(snap["reset_in_seconds"] / 60))
        raise ApiError(
            "RATE_LIMITED",
            f"Free demo limit reached ({snap['limit']} scans per hour). "
            f"Your next free slot opens in about {mins} min.",
            headers={"Retry-After": str(snap["reset_in_seconds"])},
        )


def record(owner: str) -> Optional[Dict[str, float]]:
    """Count one successful scan and return the new allowance."""
    if not config.DEMO_MODE:
        return None
    now = time.time()
    with db.connect() as conn:
        conn.execute("DELETE FROM rate_limit WHERE ts<=?", (now - config.RATE_WINDOW_SECONDS,))
        conn.execute("INSERT INTO rate_limit(ip, ts) VALUES(?, ?)", (owner, now))
    return status(owner)
