"""
Scan Logger — persists every user scan to a local SQLite database.

Table: scan_logs
──────────────────────────────────────────────────────────
id              INTEGER PRIMARY KEY
timestamp       TEXT    (ISO 8601, UTC)
ip              TEXT    (client IP)
id_number       TEXT    (full ID number)
car_number      TEXT    (full car number)
clean           INTEGER (count of clean municipalities)
fine            INTEGER (count of municipalities with fines)
failed          INTEGER (count of failed municipalities)
total_fines     INTEGER (total individual fine items found)
total_amount    TEXT    (total ₪ amount, if available)
fine_munis      TEXT    (comma-separated municipality names with fines)
fine_addresses  TEXT    (comma-separated addresses from fines)
user_agent      TEXT    (browser / platform info)
platform        TEXT    (parsed: iOS / Android / Windows / macOS / Linux / Other)
latitude        REAL    (user geolocation, if provided)
longitude       REAL    (user geolocation, if provided)
results_json    TEXT    (full JSON dump of all results)
──────────────────────────────────────────────────────────
"""

import sqlite3
import json
import os
import re
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "scan_logs.db")

# ─── Schema version: bump when adding columns ────────────
_CURRENT_SCHEMA_VERSION = 2

_NEW_COLUMNS = [
    # (column_name, column_def)
    ("fine_munis", "TEXT"),
    ("fine_addresses", "TEXT"),
    ("user_agent", "TEXT"),
    ("platform", "TEXT"),
    ("latitude", "REAL"),
    ("longitude", "REAL"),
]


def _init_db():
    """Create the scan_logs table if it doesn't exist, and migrate."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                ip              TEXT,
                id_number       TEXT,
                car_number      TEXT,
                clean           INTEGER DEFAULT 0,
                fine            INTEGER DEFAULT 0,
                failed          INTEGER DEFAULT 0,
                total_fines     INTEGER DEFAULT 0,
                total_amount    TEXT,
                fine_munis      TEXT,
                fine_addresses  TEXT,
                user_agent      TEXT,
                platform        TEXT,
                latitude        REAL,
                longitude       REAL,
                results_json    TEXT
            )
        """)
        # Auto-migrate: add any missing columns to an existing table
        existing = {row[1] for row in conn.execute("PRAGMA table_info(scan_logs)").fetchall()}
        for col_name, col_type in _NEW_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE scan_logs ADD COLUMN {col_name} {col_type}")
        conn.commit()


@contextmanager
def _get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _parse_platform(ua: str) -> str:
    """Extract a human-friendly platform name from a User-Agent string."""
    if not ua:
        return "Unknown"
    ua_lower = ua.lower()
    if "iphone" in ua_lower or "ipad" in ua_lower:
        return "iOS"
    if "android" in ua_lower:
        return "Android"
    if "macintosh" in ua_lower or "mac os" in ua_lower:
        return "macOS"
    if "windows" in ua_lower:
        return "Windows"
    if "linux" in ua_lower:
        return "Linux"
    if "cros" in ua_lower:
        return "ChromeOS"
    return "Other"


def log_scan(
    ip: str,
    id_number: str,
    car_number: str,
    results: list[dict],
    summary: dict,
    user_agent: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
):
    """Log a completed scan to the database."""
    # Calculate totals + collect fine details
    total_fines = 0
    total_amount = 0.0
    fine_munis: list[str] = []
    fine_addresses: list[str] = []

    for r in results:
        if r.get("status") == "fine":
            total_fines += r.get("count", 0)
            fine_munis.append(r.get("name", ""))
            try:
                total_amount += float(r.get("amount", 0))
            except (ValueError, TypeError):
                pass
            # Collect addresses from individual fines
            for f in r.get("fines", []):
                loc = f.get("location", "")
                if loc:
                    fine_addresses.append(f"{r.get('name', '')}: {loc}")

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO scan_logs
                (timestamp, ip, id_number, car_number,
                 clean, fine, failed, total_fines, total_amount,
                 fine_munis, fine_addresses,
                 user_agent, platform, latitude, longitude,
                 results_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                ip or "",
                id_number.strip(),
                car_number.strip(),
                summary.get("clean", 0),
                summary.get("fine", 0),
                summary.get("failed", 0),
                total_fines,
                f"{total_amount:.2f}" if total_amount > 0 else "",
                ", ".join(fine_munis) if fine_munis else "",
                " | ".join(fine_addresses) if fine_addresses else "",
                user_agent,
                _parse_platform(user_agent),
                latitude,
                longitude,
                json.dumps(results, ensure_ascii=False),
            ),
        )
        conn.commit()


def get_logs(limit: int = 100, offset: int = 0) -> list[dict]:
    """Return recent scan logs, newest first."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scan_logs ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats() -> dict:
    """Return aggregate statistics."""
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                    AS total_scans,
                COUNT(DISTINCT car_number)  AS unique_cars,
                SUM(fine)                   AS total_with_fines,
                SUM(total_fines)            AS total_fine_items
            FROM scan_logs
        """).fetchone()
        return dict(row)


# Initialise DB on import
_init_db()
