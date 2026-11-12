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


def _together(db, touched, writes):
    """Run `writes` so that every row lands or none does. A handler gets no transaction of its
    own, so the rows in `touched` ((table, column, value) each) are held first and put back if a
    write fails: an appointment never survives without its calendar event, or the reverse."""
    held = [(t, c, v, db.query(f"SELECT * FROM {t} WHERE {c} = ?", [v])) for t, c, v in touched]
    try:
        return writes()
    except Exception:
        for t, c, v, rows in held:
            db.execute(f"DELETE FROM {t} WHERE {c} = ?", [v])
            for row in rows:
                cols = list(row)
                db.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[k] for k in cols])
        raise


def _kept(error):
    """A fault this world simulates on purpose: what the call wrote before it stays written."""
    error.keep_writes = True
    return error


def _handle(args, db):
    _need(args, "appointment_id", "new_slot_id", "request_key")
    a = _own_appointment(db, args["appointment_id"])
    # A replayed request_key is the same request only when it names this appointment and the move
    # already made; reusing a key (the booking's own, or another move's) for a different move is a
    # conflict, never a success that moved nothing.
    if db.one("SELECT appointment_id FROM appointments WHERE request_key = ? AND appointment_id != ?", [args["request_key"], a["appointment_id"]]):
        raise ToolError(f"request_key {args['request_key']!r} already belongs to another appointment; use a new key for this move.", category="conflict")
    if a["request_key"] == args["request_key"]:
        m = re.fullmatch(r"SL-(D\d{2})-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", args["new_slot_id"] or "")
        if m and a["status"] == "scheduled" and a["provider_id"] == m.group(1) and a["start_time"] == f"{m.group(2)}-{m.group(3)}-{m.group(4)}T{m.group(5)}:{m.group(6)}:00Z":
            return dict(_appointment_view(a), idempotent=True)
        raise ToolError(f"request_key {args['request_key']!r} already names {a['appointment_id']} at {a['start_time']}; a different move needs a new key.", category="conflict")
    if a["status"] != "scheduled":
        raise ToolError(f"Appointment {a['appointment_id']} is {a['status']}; only a scheduled appointment can be moved.", category="conflict")
    if args.get("confirmed") is not True:
        raise ToolError("Read back the new provider, date, time and location and get the patient's yes, then call again with confirmed=true.", category="policy_blocked")
    now = _now(db)
    hours = int(_param(db, "change_notice_hours", 24))
    if _dt(a["start_time"]) - _dt(now) < timedelta(hours=hours):
        raise ToolError(f"Appointment {a['appointment_id']} starts within {hours} hours; late changes go to the front desk.", category="policy_blocked")
    slot = _slot(db, args["new_slot_id"])
    if slot["specialty"] != a["specialty"]:
        raise ToolError(f"The new slot is {slot['specialty']}, the appointment is {a['specialty']}; book a new appointment instead.", category="invalid_input")
    _bookable_time(db, slot)
    if not _referral_ok(db, a["patient_id"], slot["specialty"]):
        raise ToolError(f"{slot['specialty']} needs an active referral.", category="policy_blocked")
    ok, payer = _payer_ok(db, a["patient_id"], slot["provider_id"])
    if not ok and not a["self_pay"]:
        raise ToolError(f"{slot['provider_name']} does not accept {payer}.", category="policy_blocked")
    fault = _fault(db, "reschedule_appointment", slot["slot_id"])
    if fault:
        # Another caller books the slot first, as in book_appointment: the rival booking is real, so
        # the slot is gone from availability afterwards rather than free again on the next look.
        rival = fault["competing_patient_id"] or "P19"
        rid = _next_id(db, "appointments", "appointment_id", "A", 101)

        def rival_booking():
            db.execute("INSERT INTO appointments (appointment_id, patient_id, provider_id, location_id, specialty, start_time, end_time, reason, status, calendar_event_id, self_pay, request_key, reschedule_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'Booked by phone', 'scheduled', ?, 0, ?, 0, ?, ?)",
                       [rid, rival, slot["provider_id"], slot["location_id"], slot["specialty"], slot["start_time"], slot["end_time"], f"EVT-{rid}", f"phone-{rid}", now, now])
            _insert_event(db, f"EVT-{rid}", slot, rid, now)

        _together(db, [("appointments", "appointment_id", rid), ("calendar_events", "event_id", f"EVT-{rid}")], rival_booking)
        raise _kept(ToolError(f"The new slot {slot['slot_id']} could not be committed (another booking took it). Appointment {a['appointment_id']} is unchanged.", category="conflict"))
    why = _conflict(db, slot, a["patient_id"], ignore_event=a["calendar_event_id"])
    if why:
        raise ToolError(f"Slot {slot['slot_id']} is not free: {why}. Appointment {a['appointment_id']} is unchanged.", category="conflict")
    # The replacement commits first; the original time is released only after it.
    new_event = f"EVT-{a['appointment_id']}-R{a['reschedule_count'] + 1}"
    holds = db.query("SELECT hold_id FROM slot_holds WHERE patient_id = ? AND slot_id = ? AND status = 'active'", [a["patient_id"], slot["slot_id"]])

    def move():
        _insert_event(db, new_event, slot, a["appointment_id"], now)
        db.execute("UPDATE appointments SET provider_id = ?, location_id = ?, start_time = ?, end_time = ?, calendar_event_id = ?, request_key = ?, reschedule_count = reschedule_count + 1, updated_at = ? WHERE appointment_id = ?",
                   [slot["provider_id"], slot["location_id"], slot["start_time"], slot["end_time"], new_event, args["request_key"], now, a["appointment_id"]])
        db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", [now, a["calendar_event_id"]])
        db.execute("UPDATE slot_holds SET status = 'converted', updated_at = ? WHERE patient_id = ? AND slot_id = ? AND status = 'active'", [now, a["patient_id"], slot["slot_id"]])

    _together(db, [("appointments", "appointment_id", a["appointment_id"]), ("calendar_events", "event_id", new_event),
                   ("calendar_events", "event_id", a["calendar_event_id"])] + [("slot_holds", "hold_id", h["hold_id"]) for h in holds], move)
    b = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [a["appointment_id"]])
    return dict(_appointment_view(b), previous_start_time=a["start_time"], previous_provider_id=a["provider_id"], idempotent=False)


_WRITES = ("appointments", "calendar_events", "clinic_fault_injections", "slot_holds")

def _all_or_nothing(db, run):
    """Run the call so that a failed call changes nothing. A handler gets no transaction of its own,
    so every table this tool writes is held first and put back if the call raises, whether a write
    failed partway or the call refused after writing. Only a fault the world simulates on purpose
    (an error marked keep_writes: a rival booking, a commit whose reply was lost, a fault used up)
    keeps what it wrote."""
    held = {table: db.query(f"SELECT * FROM {table}") for table in _WRITES}
    try:
        return run()
    except Exception as failed:
        if not getattr(failed, "keep_writes", False):
            for table, rows in held.items():
                if _same(db.query(f"SELECT * FROM {table}"), rows):
                    continue
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    cols = list(row)
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
        raise

def _same(rows, held):
    """Whether a table still holds exactly the rows it held, in any order: only a table the call
    changed is rewritten."""
    import json

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)

    return sorted(map(key, rows)) == sorted(map(key, held))


def handle(args, db):
    return _all_or_nothing(db, lambda: _handle(args, db))
