# Shared by the generator and copied verbatim into every handler that handles time.
from datetime import datetime


def _iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


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


def _staff(db, staff_id):
    row = db.one("SELECT * FROM staff WHERE staff_id = ?", [staff_id])
    if row is None or not row["active"]:
        raise ToolError(f"Staff member {staff_id!r} not found or not active.", category="not_found")
    return row


def _next_id(db, table, column, prefix, start):
    n = start
    while db.one(f"SELECT 1 AS x FROM {table} WHERE {column} = ?", [f"{prefix}{n}"]):
        n += 1
    return f"{prefix}{n}"


iso = _iso


_LANGUAGES = {"english": "en", "spanish": "es", "hindi": "hi", "french": "fr", "en": "en", "es": "es", "hi": "hi", "fr": "fr"}


def _caller(db):
    """Who is in this conversation, as the session knows it (session_caller), never as the
    conversation tells it: a customer, a front-desk member, or nobody identified."""
    row = db.one("SELECT customer_id, desk_id FROM session_caller WHERE id = 'default'")
    return (row["customer_id"], row["desk_id"]) if row else (None, None)


def _desk(db):
    """Whether the caller is an active front-desk member, who may act for any customer."""
    _, desk_id = _caller(db)
    return bool(desk_id and db.one("SELECT 1 AS x FROM desk_staff WHERE desk_id = ? AND role = 'front_desk' AND active = 1", [desk_id]))


def _entitled(db, customer_id):
    """Refuse unless the caller is this customer or the front desk: a customer ID the agent passes
    names a record, it does not prove who is asking."""
    if _desk(db):
        return
    caller, _ = _caller(db)
    if not caller or caller != customer_id:
        raise ToolError(f"Permission denied: the person in this conversation ({caller or 'not identified'}) cannot see or change {customer_id}'s records.", category="permission_denied")


def handle(args, db):
    cid, kind, value = args.get("customer_id"), args.get("kind"), args.get("value")
    if kind not in ("language", "staff", "location", "time_of_day", "accessibility", "contact_channel") or not value:
        raise ToolError("kind must be one of language, staff, location, time_of_day, accessibility, contact_channel, with a value.", category="invalid_input")
    _customer(db, cid)
    _entitled(db, cid)
    if kind == "language":
        # Stored as the code staff languages use (find_staff filters by it), whichever way it was said.
        value = _LANGUAGES.get(str(value).strip().lower(), str(value).strip().lower())
        if value not in _LANGUAGES.values():
            raise ToolError(f"Unknown language {args.get('value')!r}; use one of {sorted(_LANGUAGES.values())}.", category="invalid_input")
    if kind == "staff":
        _staff(db, value)
    if kind == "location":
        _location(db, value)
    now = iso(_now(db))
    old = db.one("SELECT * FROM customer_preferences WHERE customer_id = ? AND kind = ?", [cid, kind])
    if old:
        db.execute("UPDATE customer_preferences SET value = ?, updated_at = ? WHERE pref_id = ?", [value, now, old["pref_id"]])
    else:
        pid = _next_id(db, "customer_preferences", "pref_id", "PF", 600)
        db.execute("INSERT INTO customer_preferences (pref_id, customer_id, kind, value, updated_at) VALUES (?, ?, ?, ?, ?)", [pid, cid, kind, value, now])
    return {"customer_id": cid, "kind": kind, "value": value, "replaced": bool(old)}
