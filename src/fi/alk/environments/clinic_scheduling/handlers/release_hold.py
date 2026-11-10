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


def _verified(db, patient_id):
    if not db.one("SELECT patient_id FROM patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id!r} not found.", category="not_found")
    if not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id} has not been verified in this conversation; verify the patient's full name and date of birth first.", category="permission_denied")


def _own_appointment(db, appointment_id):
    appt = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [appointment_id])
    if appt is None:
        raise ToolError(f"Appointment {appointment_id!r} not found.", category="not_found")
    _verified(db, appt["patient_id"])
    return appt


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


def _bookable_time(db, slot):
    now = _dt(_now(db))
    start = _dt(slot["start_time"])
    if start < now + timedelta(hours=int(_param(db, "min_notice_hours", 2))):
        raise ToolError(f"Slot {slot['slot_id']} starts too soon or is in the past; bookings need {_param(db, 'min_notice_hours', 2)} hours' notice.", category="policy_blocked")
    if start > now + timedelta(days=int(_param(db, "max_horizon_days", 30))):
        raise ToolError(f"Slot {slot['slot_id']} is beyond the {_param(db, 'max_horizon_days', 30)}-day booking horizon.", category="policy_blocked")


def _referral_ok(db, patient_id, specialty):
    spec = db.one("SELECT * FROM specialties WHERE specialty = ?", [specialty])
    if not spec or not spec["referral_required"]:
        return True
    today = _now(db)[:10]
    return db.one("SELECT referral_id FROM referrals WHERE patient_id = ? AND specialty = ? AND status = 'active' AND (expires_on IS NULL OR expires_on >= ?)",
                  [patient_id, specialty, today]) is not None


def _payer_ok(db, patient_id, provider_id):
    ins = db.one("SELECT payer FROM insurance WHERE patient_id = ?", [patient_id])
    if ins is None:
        return False, None
    return db.one("SELECT payer FROM provider_payers WHERE provider_id = ? AND payer = ?", [provider_id, ins["payer"]]) is not None, ins["payer"]


def _next_id(db, table, column, prefix, start):
    taken = {r[column] for r in db.query(f"SELECT {column} FROM {table}")}
    n = start + len(taken)
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _appointment_view(a):
    return {k: a[k] for k in ("appointment_id", "patient_id", "provider_id", "location_id", "specialty", "start_time", "end_time", "reason", "status", "calendar_event_id")}


def _insert_event(db, event_id, slot, appointment_id, now):
    db.execute(
        "INSERT INTO calendar_events (event_id, calendar_id, summary, description, location, start_time, end_time, time_zone, status, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'Europe/London', 'confirmed', 1, ?, ?)",
        [event_id, slot["calendar_id"], f"Clinic appointment {appointment_id}", f"appointment_id: {appointment_id}", slot["location_id"], slot["start_time"], slot["end_time"], now, now])


def _fault(db, operation, target):
    f = db.one("SELECT * FROM clinic_fault_injections WHERE operation = ? AND target_id = ? AND is_active = 1", [operation, target])
    if f:
        db.execute("UPDATE clinic_fault_injections SET is_active = 0 WHERE fault_id = ?", [f["fault_id"]])
    return f

def handle(args, db):
    _need(args, "hold_id")
    h = db.one("SELECT * FROM slot_holds WHERE hold_id = ?", [args["hold_id"]])
    if h is None:
        raise ToolError(f"Hold {args['hold_id']!r} not found.", category="not_found")
    _verified(db, h["patient_id"])
    if h["status"] == "released":
        return {"hold_id": h["hold_id"], "status": "released", "idempotent": True}
    if h["status"] != "active":
        raise ToolError(f"Hold {h['hold_id']} is {h['status']}; nothing to release.", category="conflict")
    db.execute("UPDATE slot_holds SET status = 'released', updated_at = ? WHERE hold_id = ?", [_now(db), h["hold_id"]])
    return {"hold_id": h["hold_id"], "status": "released", "idempotent": False}
