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


def _local(utc, tz):
    off = _offset_min(tz, utc)
    return (utc + timedelta(minutes=off)).strftime("%Y-%m-%dT%H:%M"), off


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


def _customer(db, customer_id):
    row = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if row is None:
        raise ToolError(f"Customer {customer_id!r} not found.", category="not_found")
    return row


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


def _staff(db, staff_id):
    row = db.one("SELECT * FROM staff WHERE staff_id = ?", [staff_id])
    if row is None or not row["active"]:
        raise ToolError(f"Staff member {staff_id!r} not found or not active.", category="not_found")
    return row


def _appointment(db, appointment_id, customer_id):
    row = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [appointment_id])
    if row is None:
        raise ToolError(f"Appointment {appointment_id!r} not found.", category="not_found")
    if row["customer_id"] != customer_id:
        raise ToolError(f"Appointment {appointment_id} does not belong to customer {customer_id}.", category="permission_denied")
    return row


def _next_id(db, table, column, prefix, start):
    n = start
    while db.one(f"SELECT 1 AS x FROM {table} WHERE {column} = ?", [f"{prefix}{n}"]):
        n += 1
    return f"{prefix}{n}"


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


def _view(a):
    return {k: a[k] for k in ("appointment_id", "customer_id", "service_id", "staff_id", "location_id", "start_utc", "end_utc", "local_start", "utc_offset", "time_zone", "status", "calendar_event_id", "rescheduled_from", "late_fee_cents")}


def _write_event(db, appt_id, staff, service, customer, loc, utc, now):
    end = utc + timedelta(minutes=service["duration_minutes"])
    event_id = f"EV-{appt_id}"
    db.execute(
        "INSERT INTO calendar_events (event_id, calendar_id, summary, description, location, start_time, end_time, time_zone, status, recurring_event_id, recurrence_rule, original_start_time, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', NULL, NULL, NULL, 1, ?, ?)",
        [event_id, staff["calendar_id"], f"{service['name']} - {customer['name']}", f"appointment:{appt_id}", loc["name"], iso(utc), iso(end), loc["time_zone"], iso(now), iso(now)],
    )
    att = db.one("SELECT COALESCE(MAX(id), 0) + 1 AS c FROM calendar_event_attendees")["c"]
    db.execute("INSERT INTO calendar_event_attendees (id, event_id, email, display_name, response_status, is_organizer, is_optional) VALUES (?, ?, ?, ?, 'accepted', FALSE, FALSE)", [att, event_id, customer["email"], customer["name"]])
    return event_id


def _insert_appointment(db, appt_id, customer, service, staff, loc, utc, off, now, request_key, source, rescheduled_from=None):
    local, _ = _local(utc, loc["time_zone"])
    event_id = _write_event(db, appt_id, staff, service, customer, loc, utc, now)
    db.execute(
        "INSERT INTO appointments (appointment_id, customer_id, service_id, staff_id, location_id, start_utc, end_utc, local_start, utc_offset, time_zone, status, calendar_event_id, request_key, rescheduled_from, source, late_fee_cents, created_at, updated_at, cancelled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'booked', ?, ?, ?, ?, 0, ?, ?, NULL)",
        [appt_id, customer["customer_id"], service["service_id"], staff["staff_id"], loc["location_id"], iso(utc), iso(utc + timedelta(minutes=service["duration_minutes"])), local, _fmt_off(off), loc["time_zone"], event_id, request_key, rescheduled_from, source, iso(now), iso(now)],
    )
    return db.one("SELECT * FROM appointments WHERE appointment_id = ?", [appt_id])


def _contention(db, staff, service, loc, utc, now):
    """A competing booking armed for this slot commits first (the concurrency fixture)."""
    c = db.one("SELECT * FROM booking_contention WHERE staff_id = ? AND start_utc = ? AND status = 'armed'", [staff["staff_id"], iso(utc)])
    if not c:
        return None
    rival = db.one("SELECT * FROM customers WHERE customer_id = ?", [c["competitor_customer_id"]])
    appt_id = _next_id(db, "appointments", "appointment_id", "A", 700)
    _insert_appointment(db, appt_id, rival, service, staff, loc, utc, _offset_min(loc["time_zone"], utc), now, f"RIVAL-{c['contention_id']}", "competitor")
    db.execute("UPDATE booking_contention SET status = 'fired' WHERE contention_id = ?", [c["contention_id"]])
    return appt_id


def handle(args, db):
    a = _appointment(db, args.get("appointment_id"), args.get("customer_id"))
    if a["status"] != "booked":
        raise ToolError(f"Appointment {a['appointment_id']} is already {a['status']}.", category="conflict")
    if args.get("confirmed") is not True:
        raise ToolError("Cancel only after the customer confirmed which appointment to cancel.", category="policy_blocked")
    now = _now(db)
    window, fee = int(_param(db, "late_cancel_window_hours", 24)), int(_param(db, "late_cancel_fee_cents", 2500))
    hours = (_dt(a["start_utc"]) - now).total_seconds() / 3600
    late_fee = fee if 0 < hours < window else 0
    if late_fee and args.get("acknowledged_fee") is not True:
        raise ToolError(f"This is a late cancellation ({hours:.1f} hours before the start): a ${late_fee / 100:.2f} fee applies. Disclose it and cancel only if the customer accepts.", category="policy_blocked")
    db.execute("UPDATE appointments SET status = 'cancelled', late_fee_cents = ?, cancelled_at = ?, updated_at = ? WHERE appointment_id = ?", [late_fee, iso(now), iso(now), a["appointment_id"]])
    db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", [iso(now), a["calendar_event_id"]])
    return {"appointment_id": a["appointment_id"], "status": "cancelled", "late_fee_cents": late_fee}
