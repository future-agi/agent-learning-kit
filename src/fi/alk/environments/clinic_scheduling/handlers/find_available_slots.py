import json
import re
from datetime import datetime, timedelta, date


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return row["now"] if row else "2026-10-26T09:00:00Z"


def _dt(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _param(db, name, default):
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


def _need(args, *names):
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}", category="invalid_input")


def _slot(db, slot_id):
    """A slot id names a provider and a start: SL-<provider>-<YYYYMMDD>-<HHMM>. It is real when it lies
    inside one of the provider's sessions; its location is that session's."""
    m = re.fullmatch(r"SL-(D\d{2})-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", slot_id or "")
    if not m:
        raise ToolError(f"Unknown slot id {slot_id!r}; slot ids come from find_available_slots.", category="invalid_input")
    prov, y, mo, d, hh, mi = m.groups()
    provider = db.one("SELECT * FROM providers WHERE provider_id = ?", [prov])
    if provider is None:
        raise ToolError(f"Unknown provider in slot {slot_id!r}.", category="not_found")
    try:
        day = date(int(y), int(mo), int(d))
    except ValueError:
        raise ToolError(f"Unknown slot id {slot_id!r}.", category="invalid_input")
    hhmm = f"{hh}:{mi}"
    start = f"{y}-{mo}-{d}T{hh}:{mi}:00Z"
    for s in db.query("SELECT * FROM provider_sessions WHERE provider_id = ? AND weekday = ?", [prov, day.weekday()]):
        end_of = _dt(f"{y}-{mo}-{d}T{s['end_hhmm']}:00Z")
        st = _dt(start)
        offset = (st - _dt(f"{y}-{mo}-{d}T{s['start_hhmm']}:00Z")).total_seconds() / 60
        if s["start_hhmm"] <= hhmm and st + timedelta(minutes=s["slot_minutes"]) <= end_of and offset % s["slot_minutes"] == 0:
            return {"slot_id": slot_id, "provider_id": prov, "provider_name": provider["name"], "specialty": provider["specialty"],
                    "calendar_id": provider["calendar_id"], "location_id": s["location_id"], "start_time": start,
                    "end_time": _iso(st + timedelta(minutes=s["slot_minutes"]))}
    raise ToolError(f"Slot {slot_id} is outside {provider['name']}'s clinic sessions.", category="not_found")


def _conflict(db, slot, patient_id=None, ignore_event=None):
    """Why the slot is not free: an event on the provider's calendar, or another patient's live hold."""
    for e in db.query("SELECT event_id, summary FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled' AND start_time < ? AND end_time > ?",
                      [slot["calendar_id"], slot["end_time"], slot["start_time"]]):
        if e["event_id"] != ignore_event:
            return f"the provider's calendar already has {e['event_id']} at that time"
    now = _now(db)
    for h in db.query("SELECT * FROM slot_holds WHERE slot_id = ? AND status = 'active' AND expires_at > ?", [slot["slot_id"], now]):
        if h["patient_id"] != patient_id:
            return f"the slot is held for another patient until {h['expires_at']}"
    return None


def _caller(db):
    row = db.one("SELECT caller_id FROM session_caller WHERE id = 'default'")
    return row["caller_id"] if row else None


def _entitled(db, patient_id):
    """Whether the person signed in to this conversation may act for `patient_id`: the patient
    themself, or someone the clinic records as their guardian or proxy. The session says who is
    talking; a patient id, or a name and date of birth, the desk is told grants nothing."""
    caller = _caller(db)
    return caller is not None and (caller == patient_id or db.one(
        "SELECT patient_id FROM caller_access WHERE caller_id = ? AND patient_id = ?", [caller, patient_id]) is not None)


def _verified(db, patient_id):
    if not db.one("SELECT patient_id FROM patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id!r} not found.", category="not_found")
    if not _entitled(db, patient_id):
        raise ToolError(f"Permission denied: the person in this conversation is not {patient_id} and holds no recorded guardian or proxy authority for them; a relative or colleague needs proxy access, which the front desk arranges.", category="permission_denied")
    if not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id} has not been verified in this conversation; verify the patient's full name and date of birth first.", category="permission_denied")


def handle(args, db):
    _need(args, "date_from", "date_to")
    if not args.get("specialty") and not args.get("provider_id"):
        raise ToolError("Give a specialty or a provider_id.", category="invalid_input")
    try:
        d0, d1 = date.fromisoformat(args["date_from"]), date.fromisoformat(args["date_to"])
    except ValueError:
        raise ToolError("date_from and date_to must be YYYY-MM-DD.", category="invalid_input")
    if d1 < d0 or (d1 - d0).days > 31:
        raise ToolError("The range must run forward and cover at most 31 days.", category="invalid_input")
    q, p = "SELECT * FROM providers WHERE 1 = 1", []
    if args.get("provider_id"):
        q += " AND provider_id = ?"; p.append(args["provider_id"])
    if args.get("specialty"):
        q += " AND specialty = ?"; p.append(args["specialty"])
    providers = db.query(q, p)
    if args.get("patient_id"):
        # The patient's own live hold shows as free to them, so naming a patient reveals what they
        # hold: only for a patient verified by someone who may act for them.
        _verified(db, args["patient_id"])
    if not providers:
        raise ToolError("No provider matches that specialty or id.", category="not_found")
    now = _dt(_now(db))
    earliest = now + timedelta(hours=int(_param(db, "min_notice_hours", 2)))
    latest = now + timedelta(days=int(_param(db, "max_horizon_days", 30)))
    slots = []
    for prov in providers:
        sessions = db.query("SELECT * FROM provider_sessions WHERE provider_id = ?", [prov["provider_id"]])
        day = d0
        while day <= d1:
            for s in sessions:
                if s["weekday"] != day.weekday() or (args.get("location_id") and s["location_id"] != args["location_id"]):
                    continue
                t = _dt(f"{day.isoformat()}T{s['start_hhmm']}:00Z")
                end = _dt(f"{day.isoformat()}T{s['end_hhmm']}:00Z")
                while t + timedelta(minutes=s["slot_minutes"]) <= end:
                    if earliest <= t <= latest:
                        sid = f"SL-{prov['provider_id']}-{t.strftime('%Y%m%d-%H%M')}"
                        slot = _slot(db, sid)
                        if _conflict(db, slot, args.get("patient_id")) is None:
                            slots.append({k: slot[k] for k in ("slot_id", "provider_id", "provider_name", "specialty", "location_id", "start_time", "end_time")})
                    t += timedelta(minutes=s["slot_minutes"])
            day += timedelta(days=1)
    slots.sort(key=lambda s: (s["start_time"], s["provider_id"]))
    spec = db.one("SELECT referral_required FROM specialties WHERE specialty = ?", [providers[0]["specialty"]])
    return {"slots": slots[:40], "count": len(slots), "computed_from": "provider calendars and clinic sessions",
            "referral_required": bool(spec and spec["referral_required"])}
