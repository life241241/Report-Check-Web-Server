"""
Scan Logger — persists every user scan to Supabase (PostgreSQL).

Table: scan_logs (in Supabase)
──────────────────────────────────────────────────────────
id              BIGSERIAL PRIMARY KEY
created_at      TIMESTAMPTZ (auto, server default)
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
latitude        DOUBLE PRECISION (user geolocation, if provided)
longitude       DOUBLE PRECISION (user geolocation, if provided)
results_json    TEXT    (full JSON dump of all results)
──────────────────────────────────────────────────────────

Environment variables (set in .env or hosting platform):
    SUPABASE_URL          — project URL   (e.g. https://xxx.supabase.co)
    SUPABASE_SERVICE_KEY  — service_role secret key
"""

import json
import os
from datetime import datetime, timezone
from supabase import create_client, Client

# ─── Supabase connection ─────────────────────────────────
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://qncujyrppnvkrzquwfpx.supabase.co",
)
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFuY3VqeXJwcG52a3J6cXV3ZnB4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MjEwNzMxNywiZXhwIjoyMDg3NjgzMzE3fQ.RwjIjzy4EDpokiQR4AyTVkeGtKzZ8WlWn3R6HfcuHjM",
)

_supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLE = "scan_logs"


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
    """Log a completed scan to Supabase."""
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
            for f in r.get("fines", []):
                loc = f.get("location", "")
                if loc:
                    fine_addresses.append(f"{r.get('name', '')}: {loc}")

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ip": ip or "",
        "id_number": id_number.strip(),
        "car_number": car_number.strip(),
        "clean": summary.get("clean", 0),
        "fine": summary.get("fine", 0),
        "failed": summary.get("failed", 0),
        "total_fines": total_fines,
        "total_amount": f"{total_amount:.2f}" if total_amount > 0 else "",
        "fine_munis": ", ".join(fine_munis) if fine_munis else "",
        "fine_addresses": " | ".join(fine_addresses) if fine_addresses else "",
        "user_agent": user_agent,
        "platform": _parse_platform(user_agent),
        "latitude": latitude,
        "longitude": longitude,
        "results_json": json.dumps(results, ensure_ascii=False),
    }

    _supabase.table(TABLE).insert(row).execute()


def get_logs(limit: int = 100, offset: int = 0) -> list[dict]:
    """Return recent scan logs, newest first."""
    result = (
        _supabase.table(TABLE)
        .select("*")
        .order("id", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data


def get_log_by_id(log_id: int) -> dict | None:
    """Return a single scan log by ID."""
    result = (
        _supabase.table(TABLE)
        .select("*")
        .eq("id", log_id)
        .execute()
    )
    return result.data[0] if result.data else None


def get_stats() -> dict:
    """Return aggregate statistics.

    Supabase REST API doesn't support raw SQL aggregates directly,
    so we fetch counts via simple queries.
    """
    # Total scans
    all_rows = _supabase.table(TABLE).select("id, car_number, fine, total_fines", count="exact").execute()
    total_scans = all_rows.count or 0

    # Unique cars
    car_numbers = set()
    total_with_fines = 0
    total_fine_items = 0
    for row in all_rows.data:
        car_numbers.add(row.get("car_number"))
        fine_val = row.get("fine") or 0
        if fine_val > 0:
            total_with_fines += 1
        total_fine_items += row.get("total_fines") or 0

    return {
        "total_scans": total_scans,
        "unique_cars": len(car_numbers),
        "total_with_fines": total_with_fines,
        "total_fine_items": total_fine_items,
    }
