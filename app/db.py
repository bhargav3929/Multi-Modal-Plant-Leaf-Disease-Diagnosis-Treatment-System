"""SQLite persistence: scan history, UI-saved API keys and the rate-limit ledger.

Every call opens a short-lived connection (cheap for SQLite) so the module is safe
to use from FastAPI's worker threads and from LangGraph nodes.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      REAL NOT NULL,
    ip              TEXT NOT NULL,
    crop            TEXT,
    city            TEXT,
    disease         TEXT,
    scientific_name TEXT,
    pathogen_class  TEXT,
    confidence      REAL,
    severity        TEXT,
    provider        TEXT,
    thumb           TEXT,
    state_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scans_ip_created ON scans (ip, created_at DESC);

CREATE TABLE IF NOT EXISTS rate_limit (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ip  TEXT NOT NULL,
    ts  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_ip_ts ON rate_limit (ip, ts);

CREATE TABLE IF NOT EXISTS demo_runs (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    tid TEXT NOT NULL,
    ts  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_demo_runs_tid ON demo_runs (tid);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def connect():
    """Yield an autocommit connection (explicit transactions via BEGIN IMMEDIATE)."""
    conn = sqlite3.connect(str(config.DB_PATH), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# --- settings (UI-saved API keys) ---------------------------------------------
def get_setting(key: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def delete_setting(key: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))


# --- scans -------------------------------------------------------------------
def save_scan(ip: str, crop: str, city: str, diagnosis: Dict[str, Any], provider: str,
              thumb: str, record: Dict[str, Any]) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO scans(created_at, ip, crop, city, disease, scientific_name, pathogen_class,"
            " confidence, severity, provider, thumb, state_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                time.time(), ip, crop or diagnosis.get("crop", ""), city,
                diagnosis.get("disease", ""), diagnosis.get("scientific_name", ""),
                diagnosis.get("pathogen_class", ""), diagnosis.get("confidence", 0),
                diagnosis.get("severity", ""), provider, thumb, json.dumps(record),
            ),
        )
        return int(cur.lastrowid)


def update_scan_record(scan_id: int, record: Dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute("UPDATE scans SET state_json=? WHERE id=?", (json.dumps(record), scan_id))


def list_scans(ip: str, query: str = "", pathogen_class: str = "", limit: int = 60) -> List[Dict[str, Any]]:
    sql = ("SELECT id, created_at, crop, city, disease, scientific_name, pathogen_class, confidence,"
           " severity, thumb FROM scans WHERE ip=?")
    args: List[Any] = [ip]
    if query:
        sql += " AND (disease LIKE ? OR crop LIKE ? OR city LIKE ? OR scientific_name LIKE ?)"
        args += [f"%{query}%"] * 4
    if pathogen_class:
        sql += " AND pathogen_class=?"
        args.append(pathogen_class)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 300)))
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_scan(ip: str, scan_id: int) -> Optional[Dict[str, Any]]:
    """Return the full stored result (with thumb, id, created_at merged in)."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM scans WHERE id=? AND ip=?", (scan_id, ip)).fetchone()
    if not row:
        return None
    result = json.loads(row["state_json"])
    result.update(scan_id=row["id"], created_at=row["created_at"], thumb=row["thumb"])
    return result


def delete_scan(ip: str, scan_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM scans WHERE id=? AND ip=?", (scan_id, ip))
        return cur.rowcount > 0
