"""Demo mode: signed tickets from lastsem.com, runs per ticket, global daily cap, 24 h cleanup.

Only used when DEMO_MODE=true. A run is recorded only after the AI work succeeds.
"""
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional, Tuple

from fastapi import Request

from . import config, db
from .errors import ApiError

log = logging.getLogger("agri")
HEADER = "X-LastSem-Ticket"
DAY = 86400


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_ticket(ticket: Optional[str], secret: str, slug: str) -> Tuple[Optional[dict], Optional[str]]:
    """Return (payload, None) if valid, else (None, error_code)."""
    if not ticket:
        return None, "TICKET_REQUIRED"
    if not secret:
        return None, "TICKET_INVALID"
    try:
        body, sig = ticket.split(".")
        payload = json.loads(_b64d(body))
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if payload.get("v") != 1 or not hmac.compare_digest(expected, _b64d(sig)):
            return None, "TICKET_INVALID"
    except Exception:
        return None, "TICKET_INVALID"
    if payload.get("slug") not in (slug, "*"):
        return None, "TICKET_INVALID"
    if int(payload.get("exp", 0)) < time.time():
        return None, "TICKET_EXPIRED"
    return payload, None


MESSAGES = {
    "TICKET_REQUIRED": "Sign in on lastsem.com to try your own input.",
    "TICKET_INVALID": "This demo ticket isn't valid. Go back to lastsem.com and start a new try.",
    "TICKET_EXPIRED": "This demo ticket has expired. Go back to lastsem.com and start a new try.",
    "TICKET_USED_UP": "You've used all the live runs on this ticket. Go back to lastsem.com for another.",
    "DAILY_CAP_REACHED": "The demo has reached its daily limit. Samples still work; please try again tomorrow.",
}


def cleanup() -> None:
    cutoff = time.time() - DAY
    with db.connect() as conn:
        conn.execute("DELETE FROM demo_runs WHERE ts<?", (cutoff,))
        conn.execute("DELETE FROM scans WHERE ip LIKE 'ticket:%' AND created_at<?", (cutoff,))


def _runs_used(tid: str) -> int:
    with db.connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM demo_runs WHERE tid=?", (tid,)).fetchone()[0])


def _runs_today() -> int:
    day_start = time.time() - time.time() % DAY
    with db.connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM demo_runs WHERE ts>=?", (day_start,)).fetchone()[0])


def read_ticket(request: Request) -> Tuple[Optional[dict], Optional[str]]:
    return verify_ticket(request.headers.get(HEADER), config.DEMO_TICKET_SECRET, config.PROJECT_SLUG)


def require_run(request: Request) -> Optional[dict]:
    """Gate a live AI run. Returns the ticket payload (demo) or None (download). Raises ApiError."""
    if not config.DEMO_MODE:
        return None
    payload, err = read_ticket(request)
    if err:
        raise ApiError(err, MESSAGES[err])
    if _runs_used(payload["tid"]) >= int(payload.get("runs", 0)):
        raise ApiError("TICKET_USED_UP", MESSAGES["TICKET_USED_UP"])
    if _runs_today() >= config.DEMO_DAILY_CAP:
        raise ApiError("DAILY_CAP_REACHED", MESSAGES["DAILY_CAP_REACHED"])
    return payload


def record_run(payload: Optional[dict]) -> None:
    if not config.DEMO_MODE or not payload:
        return
    with db.connect() as conn:
        conn.execute("INSERT INTO demo_runs(tid, ts) VALUES(?, ?)", (payload["tid"], time.time()))


def info(request: Request) -> Optional[dict]:
    if not config.DEMO_MODE:
        return None
    payload, err = read_ticket(request)
    if err:
        return {"ticket_valid": False, "runs_left": 0, "daily_cap_reached": _runs_today() >= config.DEMO_DAILY_CAP}
    left = max(0, int(payload.get("runs", 0)) - _runs_used(payload["tid"]))
    return {"ticket_valid": True, "runs_left": left, "daily_cap_reached": _runs_today() >= config.DEMO_DAILY_CAP}


def no_ticket(request: Request) -> bool:
    """Demo visitor without a valid ticket: has no private history to show."""
    return config.DEMO_MODE and read_ticket(request)[0] is None


def quota(request: Request) -> Optional[dict]:
    """Runs left on this visitor's ticket (demo only; None in the download version)."""
    if not config.DEMO_MODE:
        return None
    payload, _ = read_ticket(request)
    if not payload:
        return None
    limit = int(payload.get("runs", 0))
    used = _runs_used(payload["tid"])
    return {"limit": limit, "used": used, "remaining": max(0, limit - used),
            "reset_in_seconds": max(0, int(payload.get("exp", 0) - time.time())), "window_seconds": 900}


def owner_id(request: Request) -> str:
    """Who owns history/quota rows: the ticket id in demo mode, the client address otherwise."""
    if config.DEMO_MODE:
        payload, _ = read_ticket(request)
        return f"ticket:{payload['tid']}" if payload else "ticket:none"
    return client_ip(request)


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY and config.DEMO_MODE:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
