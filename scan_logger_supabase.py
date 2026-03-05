"""
Scan Logger — persists every user scan to Supabase (PostgreSQL).

Table: scan_logs (in Supabase)
──────────────────────────────────────────────────────────
id              BIGSERIAL PRIMARY KEY
created_at      TIMESTAMPTZ (auto, server default)
vehicle         JSONB  {car_number, manufacturer, model}
user_info       JSONB  {id_number, first_name, last_name, email}
fines           JSONB  {total_fines, total_amount, clean_count, fine_count,
                        failed_count, municipalities: [...]}
check_metadata  JSONB  {timestamp, ip, platform, user_agent,
                        location: {latitude, longitude}, raw_results: [...]}
──────────────────────────────────────────────────────────

Environment variables (set in .env or hosting platform):
    SUPABASE_URL          — project URL   (e.g. https://xxx.supabase.co)
    SUPABASE_SERVICE_KEY  — service_role secret key
"""

import os
from datetime import datetime, timezone
from supabase import create_client, Client

# ─── Supabase connection ─────────────────────────────────
# In production: set via Railway dashboard environment variables.
# In local dev:  create a .env file (already gitignored) with:
#   SUPABASE_URL=https://xxx.supabase.co
#   SUPABASE_SERVICE_KEY=your-key-here

# Load .env file if present (local dev only)
from dotenv import load_dotenv
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY. "
        "Set them as environment variables or in a .env file."
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
    """Log a completed scan to Supabase with structured JSONB columns."""
    # ── Build municipalities list for fines ──
    municipalities: list[dict] = []
    total_fines = 0
    total_amount = 0.0

    for r in results:
        if r.get("status") == "fine":
            total_fines += r.get("count", 0)
            try:
                total_amount += float(r.get("amount", 0))
            except (ValueError, TypeError):
                pass
            muni: dict = {
                "name": r.get("name", ""),
                "count": r.get("count", 0),
            }
            if r.get("fines"):
                muni["fines"] = r["fines"]
            if r.get("payment_url"):
                muni["payment_url"] = r["payment_url"]
            if r.get("person_name"):
                muni["person_name"] = r["person_name"]
            municipalities.append(muni)

    # ── vehicle ──
    vehicle = {
        "car_number": car_number.strip(),
    }

    # ── user_info ──
    user_info = {
        "id_number": id_number.strip(),
    }

    # ── fines ──
    fines = {
        "total_fines": total_fines,
        "total_amount": total_amount if total_amount > 0 else 0,
        "clean_count": summary.get("clean", 0),
        "fine_count": summary.get("fine", 0),
        "failed_count": summary.get("failed", 0),
        "municipalities": municipalities,
    }

    # ── check_metadata ──
    location = None
    if latitude is not None and longitude is not None:
        location = {"latitude": latitude, "longitude": longitude}

    check_metadata = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ip": ip or "",
        "platform": _parse_platform(user_agent),
        "user_agent": user_agent,
        "location": location,
        "raw_results": results,
    }

    row = {
        "vehicle": vehicle,
        "user_info": user_info,
        "fines": fines,
        "check_metadata": check_metadata,
    }

    result = _supabase.table(TABLE).insert(row).execute()
    if result.data:
        return result.data[0].get("id")
    return None


def update_scan_subscriber(
    scan_id: int,
    email: str,
    first_name: str = "",
    last_name: str = "",
) -> dict | None:
    """Merge subscriber info into the user_info JSONB of a scan log row."""
    # Read current user_info to preserve id_number
    current = _supabase.table(TABLE).select("user_info").eq("id", scan_id).execute()
    user_info = current.data[0].get("user_info", {}) if current.data else {}
    user_info["email"] = email.strip().lower()
    user_info["first_name"] = first_name.strip() if first_name else ""
    user_info["last_name"] = last_name.strip() if last_name else ""

    result = (
        _supabase.table(TABLE)
        .update({"user_info": user_info})
        .eq("id", scan_id)
        .execute()
    )
    return result.data[0] if result.data else None


def update_scan_vehicle(
    scan_id: int,
    manufacturer: str = "",
    model: str = "",
) -> dict | None:
    """Merge vehicle manufacturer & model into the vehicle JSONB."""
    # Read current vehicle to preserve car_number
    current = _supabase.table(TABLE).select("vehicle").eq("id", scan_id).execute()
    vehicle = current.data[0].get("vehicle", {}) if current.data else {}
    vehicle["manufacturer"] = manufacturer.strip() if manufacturer else ""
    vehicle["model"] = model.strip() if model else ""

    result = (
        _supabase.table(TABLE)
        .update({"vehicle": vehicle})
        .eq("id", scan_id)
        .execute()
    )
    return result.data[0] if result.data else None


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


SUBSCRIBERS_TABLE = "subscribers"


def save_subscriber(email: str, first_name: str = "", last_name: str = "") -> dict:
    """Save a new newsletter subscriber to Supabase.

    Table: subscribers
    ──────────────────────────────────────────────────────────
    id              BIGSERIAL PRIMARY KEY
    created_at      TIMESTAMPTZ (auto, server default)
    email           TEXT NOT NULL UNIQUE
    first_name      TEXT
    last_name       TEXT
    ──────────────────────────────────────────────────────────
    """
    row = {
        "email": email.strip().lower(),
        "first_name": first_name.strip() if first_name else "",
        "last_name": last_name.strip() if last_name else "",
    }
    result = _supabase.table(SUBSCRIBERS_TABLE).insert(row).execute()
    return result.data[0] if result.data else row


def get_stats() -> dict:
    """Return aggregate statistics."""
    all_rows = _supabase.table(TABLE).select("id, vehicle, fines", count="exact").execute()
    total_scans = all_rows.count or 0

    car_numbers = set()
    total_with_fines = 0
    total_fine_items = 0
    for row in all_rows.data:
        v = row.get("vehicle") or {}
        car_numbers.add(v.get("car_number", ""))
        f = row.get("fines") or {}
        if (f.get("fine_count") or 0) > 0:
            total_with_fines += 1
        total_fine_items += f.get("total_fines") or 0

    return {
        "total_scans": total_scans,
        "unique_cars": len(car_numbers),
        "total_with_fines": total_with_fines,
        "total_fine_items": total_fine_items,
    }
