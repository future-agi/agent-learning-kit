# Shared by the generator and copied verbatim into every handler that handles time.
from datetime import datetime, timedelta


def _nth_sunday(year, month, n):
    d = datetime(year, month, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7) + timedelta(weeks=n - 1)


def _last_sunday(year, month):
    d = (datetime(year, month + 1, 1) - timedelta(days=1)) if month < 12 else datetime(year, 12, 31)
    return d - timedelta(days=(d.weekday() - 6) % 7)


def _offset_min(tz, utc):
    """Minutes east of UTC in force in `tz` at the naive UTC instant `utc`."""
    y = utc.year
    if tz == "America/New_York":
        start = _nth_sunday(y, 3, 2) + timedelta(hours=7)
        end = _nth_sunday(y, 11, 1) + timedelta(hours=6)
        return -240 if start <= utc < end else -300
    if tz == "Europe/London":
        start = _last_sunday(y, 3) + timedelta(hours=1)
        end = _last_sunday(y, 10) + timedelta(hours=1)
        return 60 if start <= utc < end else 0
    return 0


def _fmt_off(m):
    sign = "+" if m >= 0 else "-"
    m = abs(m)
    return f"{sign}{m // 60:02d}:{m % 60:02d}"


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


def _location(db, location_id):
    row = db.one("SELECT * FROM locations WHERE location_id = ?", [location_id])
    if row is None:
        raise ToolError(f"Location {location_id!r} not found.", category="not_found")
    return row


def handle(args, db):
    loc = _location(db, args.get("location_id"))
    now = _now(db)
    changes = []
    for d in range(0, 40):
        a, b = now + timedelta(days=d), now + timedelta(days=d + 1)
        if _offset_min(loc["time_zone"], a) != _offset_min(loc["time_zone"], b):
            changes.append({"around": b.strftime("%Y-%m-%d"), "from": _fmt_off(_offset_min(loc["time_zone"], a)), "to": _fmt_off(_offset_min(loc["time_zone"], b))})
    closed = [{"date": r["date"], "reason": r["reason"]} for r in db.query("SELECT date, reason FROM blackout_dates WHERE location_id = ? ORDER BY date", [loc["location_id"]])]
    return {"location_id": loc["location_id"], "name": loc["name"], "address": loc["address"], "time_zone": loc["time_zone"], "utc_offset_now": _fmt_off(_offset_min(loc["time_zone"], now)),
            "hours": {"monday_to_friday": f"{loc['weekday_open']}-{loc['weekday_close']}", "saturday": f"{loc['saturday_open']}-{loc['saturday_close']}" if loc["saturday_open"] else "closed", "sunday": "closed"},
            "closed_dates": closed, "clock_changes": changes}
