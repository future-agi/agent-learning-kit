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


def _next_id(db, table, column, prefix, start):
    taken = {r[column] for r in db.query(f"SELECT {column} FROM {table}")}
    n = start + len(taken)
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _handle(args, db):
    _need(args, "patient_id", "slot_id", "request_key")
    pid = args["patient_id"]
    _verified(db, pid)
    same = db.one("SELECT * FROM slot_holds WHERE request_key = ?", [args["request_key"]])
    if same and (same["patient_id"] != pid or same["slot_id"] != args["slot_id"]):
        # Another patient's or another slot's hold under this key: replaying it would hand back a
        # hold this request never placed.
        raise ToolError(f"request_key {args['request_key']!r} was already used for another hold; use a new key for this one.", category="conflict")
    if same:
        return {"hold_id": same["hold_id"], "slot_id": same["slot_id"], "status": same["status"], "expires_at": same["expires_at"], "idempotent": True}
    slot = _slot(db, args["slot_id"])
    _bookable_time(db, slot)
    why = _conflict(db, slot, pid)
    if why:
        raise ToolError(f"Slot {slot['slot_id']} cannot be held: {why}.", category="conflict")
    now = _now(db)
    if db.one("SELECT hold_id FROM slot_holds WHERE patient_id = ? AND status = 'active' AND expires_at > ?", [pid, now]):
        raise ToolError(f"Patient {pid} already holds a slot; release it first.", category="conflict")
    expires = _iso(_dt(now) + timedelta(minutes=int(_param(db, "hold_minutes", 15))))
    hid = _next_id(db, "slot_holds", "hold_id", "H", 101)
    db.execute("INSERT INTO slot_holds (hold_id, patient_id, slot_id, provider_id, start_time, status, expires_at, request_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
               [hid, pid, slot["slot_id"], slot["provider_id"], slot["start_time"], expires, args["request_key"], now, now])
    return {"hold_id": hid, "slot_id": slot["slot_id"], "status": "active", "expires_at": expires, "idempotent": False}


_WRITES = ("slot_holds",)

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
