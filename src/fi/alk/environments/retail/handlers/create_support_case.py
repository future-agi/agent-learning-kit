def _caller(db):
    """The customer signed in to this conversation. The session sets it; an ID, email or name the
    desk is told never changes who it is talking to."""
    row = db.one("SELECT customer_id FROM session_caller WHERE id = 'default'")
    return row["customer_id"] if row else None


def _for_caller(db, customer_id, what):
    """Act only for the signed-in customer: a customer_id argument is checked, never trusted."""
    caller = _caller(db)
    if not caller or str(customer_id or "").strip() != caller:
        raise ToolError(f"Permission denied: {what} is not the signed-in customer's ({caller or 'nobody is signed in'}).", category="permission_denied")


def _handle(args, db):
    customer_id = args.get("customer_id")
    subject = args.get("subject")
    details = args.get("details")
    order_id = args.get("order_id")
    
    if not customer_id or not subject or not details:
        raise ToolError("customer_id, subject, and details are required.", category="invalid_input")
        
    cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if not cust:
        raise ToolError(f"Customer {customer_id} not found.", category="not_found")
        
    _for_caller(db, customer_id, f"customer {customer_id}")
    if order_id:
        ord_row = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
        if not ord_row:
            raise ToolError(f"Order {order_id} not found.", category="not_found")
        if ord_row["customer_id"] != customer_id:
            raise ToolError(f"Order {order_id} is not an order of customer {customer_id}.", category="permission_denied")
            
    clock_row = db.one("SELECT now FROM clock")
    created_at = clock_row["now"]
    
    case_count = db.one("SELECT COUNT(*) as count FROM support_cases")["count"]
    case_id = f"SC{case_count + 1:02d}"
    while db.one("SELECT * FROM support_cases WHERE case_id = ?", [case_id]):
        case_count += 1
        case_id = f"SC{case_count + 1:02d}"
        
    db.execute(
        "INSERT INTO support_cases (case_id, customer_id, order_id, subject, details, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [case_id, customer_id, order_id, subject, details, "opened", created_at]
    )
    
    return {
        "case_id": case_id,
        "customer_id": customer_id,
        "order_id": order_id,
        "subject": subject,
        "details": details,
        "status": "opened",
        "created_at": created_at
    }


_WRITES = ("support_cases",)

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
