"""Persistent SQLite sliding-window rate limiter (default: 5 requests / hour / IP).

Each consumed request is a row (ip, timestamp). A request is allowed when fewer than
`limit` rows for that IP exist inside the last `window` seconds. Because state lives in
SQLite the quota survives server restarts, and BEGIN IMMEDIATE makes check+insert atomic.
"""
import time
from typing import Dict

from fastapi import HTTPException

from . import config, db


def _snapshot(conn, ip: str, now: float) -> Dict[str, float]:
    cutoff = now - config.RATE_WINDOW_SECONDS
    row = conn.execute(
        "SELECT COUNT(*) AS n, MIN(ts) AS oldest FROM rate_limit WHERE ip=? AND ts>?", (ip, cutoff)
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


def status(ip: str) -> Dict[str, float]:
    """Current quota without consuming anything."""
    with db.connect() as conn:
        return _snapshot(conn, ip, time.time())


def consume(ip: str) -> Dict[str, float]:
    """Consume one request or raise HTTP 429 with a friendly message."""
    now = time.time()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Housekeeping: drop rows no IP can still need.
            conn.execute("DELETE FROM rate_limit WHERE ts<=?", (now - config.RATE_WINDOW_SECONDS,))
            snap = _snapshot(conn, ip, now)
            if snap["remaining"] <= 0:
                conn.execute("COMMIT")
                mins = max(1, round(snap["reset_in_seconds"] / 60))
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Free demo limit reached ({snap['limit']} AI requests per hour). "
                        f"Your next free slot opens in about {mins} min. "
                        "This cap protects the shared free-tier AI quota."
                    ),
                    headers={"Retry-After": str(snap["reset_in_seconds"])},
                )
            conn.execute("INSERT INTO rate_limit(ip, ts) VALUES(?, ?)", (ip, now))
            conn.execute("COMMIT")
        except HTTPException:
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise
    # Recompute so the response reflects the request we just consumed.
    return status(ip)
