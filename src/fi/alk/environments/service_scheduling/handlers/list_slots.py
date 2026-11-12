# Shared by the generator and copied verbatim into every handler that handles time.
import json, re
from datetime import datetime, timedelta

ZONES = {"America/New_York": (-300, -240), "Europe/London": (0, 60)}


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


def _parse_off(s):
    m = re.match(r"^([+-])(\d{2}):(\d{2})$", str(s or ""))
    if not m:
        raise ToolError(f"utc_offset must look like -04:00 or +01:00: {s!r}", category="invalid_input")
    return (-1 if m.group(1) == "-" else 1) * (int(m.group(2)) * 60 + int(m.group(3)))


def _to_utc(local, tz, utc_offset=None):
    """A local wall-clock time in `tz` ('YYYY-MM-DDTHH:MM', optionally with an offset or Z) as a
    naive UTC instant and the offset in force. An ambiguous time (the repeated hour when clocks go
    back) is refused unless `utc_offset` says which occurrence; a time that does not exist is refused."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::\d{2})?(Z|[+-]\d{2}:\d{2})?$", str(local or "").strip())
    if not m:
        raise ToolError(f"start time must be a local time like 2026-10-29T14:00: {local!r}", category="invalid_input")
    naive = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}")
    suffix = m.group(4)
    if suffix == "Z":
        return naive, _offset_min(tz, naive)
    if suffix:
        utc_offset = suffix
    candidates = []
    for o in ZONES.get(tz, (0, 0)):
        utc = naive - timedelta(minutes=o)
        if _offset_min(tz, utc) == o and (utc, o) not in candidates:
            candidates.append((utc, o))
    candidates.sort()
    if utc_offset is not None and utc_offset != "":
        want = _parse_off(utc_offset)
        for utc, o in candidates:
            if o == want:
                return utc, o
        raise ToolError(f"{naive:%Y-%m-%d %H:%M} in {tz} is not at UTC{_fmt_off(want)}; valid: {[ _fmt_off(o) for _, o in candidates]}", category="invalid_input")
    if not candidates:
        raise ToolError(f"{naive:%Y-%m-%d %H:%M} does not exist in {tz}: the clocks go forward over it.", category="invalid_input")
    if len(candidates) > 1:
        raise ToolError(
            f"{naive:%Y-%m-%d %H:%M} happens twice in {tz} because the clocks go back: once at UTC{_fmt_off(candidates[0][1])} "
            f"and once at UTC{_fmt_off(candidates[1][1])}. Ask which one is meant and pass utc_offset.",
            category="invalid_input",
        )
    return candidates[0]


def _iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


def _param(db, name, default):
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


def _location(db, location_id):
    row = db.one("SELECT * FROM locations WHERE location_id = ?", [location_id])
    if row is None:
        raise ToolError(f"Location {location_id!r} not found.", category="not_found")
    return row


def _service(db, service_id):
    row = db.one("SELECT * FROM services WHERE service_id = ?", [service_id])
    if row is None:
        raise ToolError(f"Service {service_id!r} not found.", category="not_found")
    return row


def _hours(loc, day):
    wd = day.weekday()
    if wd < 5:
        return loc["weekday_open"], loc["weekday_close"]
    if wd == 5 and loc["saturday_open"]:
        return loc["saturday_open"], loc["saturday_close"]
    return None, None


def _bookable(db, staff, service, loc, utc, off, *, ignore_event=None, customer_id=None, hold_id=None):
    """Why this start cannot be booked, or None. Availability comes from the staff calendar."""
    tz = loc["time_zone"]
    if staff["location_id"] != loc["location_id"]:
        return f"{staff['name']} works at {staff['location_id']}, not {loc['location_id']}"
    if service["service_id"] not in staff["services"].split(","):
        return f"{staff['name']} does not offer {service['name']}"
    local = utc + timedelta(minutes=off)
    step = int(_param(db, "slot_interval_minutes", 15))
    if local.minute % step:
        return f"start times are on the {step}-minute grid"
    opens, closes = _hours(loc, local)
    end_local = local + timedelta(minutes=service["duration_minutes"])
    if not opens or local.strftime("%H:%M") < opens or end_local.strftime("%H:%M") > closes or end_local.date() != local.date():
        return f"{loc['name']} is closed then (hours {opens or 'closed'}-{closes or ''} local)"
    if db.one("SELECT 1 AS x FROM blackout_dates WHERE location_id = ? AND date = ?", [loc["location_id"], local.strftime("%Y-%m-%d")]):
        return f"{loc['name']} is closed on {local:%Y-%m-%d}"
    now = _now(db)
    if utc < now + timedelta(hours=int(_param(db, "booking_min_notice_hours", 2))):
        return "too soon: bookings need the minimum notice"
    if utc > now + timedelta(days=int(_param(db, "booking_horizon_days", 30))):
        return "too far ahead: outside the booking horizon"
    start_s, end_s = iso(utc), iso(utc + timedelta(minutes=service["duration_minutes"]))
    for ev in db.query("SELECT event_id, start_time, end_time FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled'", [staff["calendar_id"]]):
        if ev["event_id"] != ignore_event and ev["start_time"] < end_s and start_s < ev["end_time"]:
            return f"{staff['name']}'s calendar is busy then ({ev['event_id']})"
    for h in db.query("SELECT * FROM holds WHERE staff_id = ? AND status = 'active'", [staff["staff_id"]]):
        if h["expires_at"] <= iso(now) or h["hold_id"] == hold_id:
            continue
        if h["start_utc"] < end_s and start_s < h["end_utc"]:
            return f"the slot is held for another customer until {h['expires_at']}"
    return None


iso = _iso


def handle(args, db):
    service, loc = _service(db, args.get("service_id")), _location(db, args.get("location_id"))
    try:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(args.get("date"))):
            raise ValueError(args.get("date"))
        day = datetime.fromisoformat(str(args.get("date")))
    except ValueError:
        raise ToolError("date must be YYYY-MM-DD", category="invalid_input")
    tz = loc["time_zone"]
    step = int(_param(db, "slot_interval_minutes", 15))
    opens, closes = _hours(loc, day)
    slots = []
    if opens:
        for st in db.query("SELECT * FROM staff WHERE active = 1 AND location_id = ? ORDER BY staff_id", [loc["location_id"]]):
            if args.get("staff_id") and st["staff_id"] != args["staff_id"]:
                continue
            if args.get("language") and args["language"] not in st["languages"].split(","):
                continue
            if service["service_id"] not in st["services"].split(","):
                continue
            cal = db.one("SELECT access_role FROM calendar_calendars WHERE calendar_id = ?", [st["calendar_id"]])
            t = datetime.fromisoformat(f"{day:%Y-%m-%d}T{opens}")
            while (t + timedelta(minutes=service["duration_minutes"])).strftime("%H:%M") <= closes and t.date() == day.date():
                try:
                    utc, off = _to_utc(t.strftime("%Y-%m-%dT%H:%M"), tz)
                except ToolError:
                    t += timedelta(minutes=step)
                    continue
                if _bookable(db, st, service, loc, utc, off) is None:
                    slots.append({"staff_id": st["staff_id"], "staff_name": st["name"], "start_local": t.strftime("%Y-%m-%dT%H:%M"), "start_utc": iso(utc), "utc_offset": _fmt_off(off), "duration_minutes": service["duration_minutes"], "online_booking": bool(cal and cal["access_role"] in ("writer", "owner"))})
                t += timedelta(minutes=step)
    out = {"date": f"{day:%Y-%m-%d}", "location_id": loc["location_id"], "time_zone": tz, "service_id": service["service_id"], "slots": slots}
    if not slots:
        # An empty day says why when the reason is the studio's, not the calendars': the agent can
        # then explain a closure, the notice or the horizon instead of guessing.
        now = _now(db)
        horizon = now + timedelta(days=int(_param(db, "booking_horizon_days", 30)))
        notice = now + timedelta(hours=int(_param(db, "booking_min_notice_hours", 2)))
        if not opens:
            out["unavailable_reason"] = f"{loc['name']} is closed on {day:%A}s"
        elif db.one("SELECT reason FROM blackout_dates WHERE location_id = ? AND date = ?", [loc["location_id"], f"{day:%Y-%m-%d}"]):
            out["unavailable_reason"] = f"{loc['name']} is closed on {day:%Y-%m-%d}: " + db.one("SELECT reason FROM blackout_dates WHERE location_id = ? AND date = ?", [loc["location_id"], f"{day:%Y-%m-%d}"])["reason"]
        else:
            first, _ = _to_utc(f"{day:%Y-%m-%d}T{opens}", tz)
            last, _ = _to_utc(f"{day:%Y-%m-%d}T{closes}", tz)
            if first > horizon:
                out["unavailable_reason"] = f"outside the booking horizon: bookings open at most {int(_param(db, 'booking_horizon_days', 30))} days ahead, up to {iso(horizon)}"
            elif last < notice:
                out["unavailable_reason"] = f"too soon: bookings need {int(_param(db, 'booking_min_notice_hours', 2))} hours' notice"
    return out
