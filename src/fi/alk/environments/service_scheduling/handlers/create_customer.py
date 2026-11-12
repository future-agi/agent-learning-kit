# Shared by the generator and copied verbatim into every handler that handles time.
from datetime import datetime

ZONES = {"America/New_York": (-300, -240), "Europe/London": (0, 60)}


def _iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


def _next_id(db, table, column, prefix, start):
    n = start
    while db.one(f"SELECT 1 AS x FROM {table} WHERE {column} = ?", [f"{prefix}{n}"]):
        n += 1
    return f"{prefix}{n}"


iso = _iso


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


def _handle(args, db):
    name, email, phone = args.get("name"), (args.get("email") or "").strip(), (args.get("phone") or "").strip()
    if not name or not email or "@" not in email or not phone:
        raise ToolError("name, a valid email and phone are required.", category="invalid_input")
    if db.one("SELECT 1 AS x FROM customers WHERE LOWER(email) = ?", [email.lower()]):
        raise ToolError("A customer with this email already exists; look them up with get_customer instead of creating another.", category="conflict")
    caller, _ = _caller(db)
    if caller and not _desk(db):
        raise ToolError(f"Permission denied: the person in this conversation already has a record ({caller}); look it up with get_customer.", category="permission_denied")
    tz = args.get("time_zone") or "America/New_York"
    if tz not in ZONES:
        raise ToolError(f"Unknown time zone {tz!r}; use America/New_York or Europe/London.", category="invalid_input")
    cid = _next_id(db, "customers", "customer_id", "CU", 600)
    now = _now(db)
    db.execute("INSERT INTO customers (customer_id, name, email, phone, preferred_language, time_zone, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", [cid, name, email, phone, args.get("preferred_language") or "en", tz, iso(now)])
    if not caller and not _desk(db):
        # The unidentified person in this conversation has just registered: the session now knows them.
        db.execute("UPDATE session_caller SET customer_id = ? WHERE id = 'default'", [cid])
    return {"customer_id": cid, "name": name, "email": email}


_WRITES = ("customers", "session_caller")

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
