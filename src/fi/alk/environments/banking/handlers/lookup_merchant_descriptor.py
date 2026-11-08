def handle(args, db):
    descriptor = args.get("descriptor")
    transaction_id = args.get("transaction_id")
    if not descriptor and not transaction_id:
        raise ToolError("Provide a statement descriptor or a transaction_id.")
    if transaction_id:
        customer_id = args.get("customer_id")
        session_id = args.get("session_id")
        if not customer_id or not session_id:
            raise ToolError("Looking up a transaction's descriptor needs customer_id and session_id.")
        sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
        if not sess:
            raise ToolError(f"Session {session_id} not found.")
        if sess["customer_id"] != customer_id:
            raise ToolError("Session customer mismatch.")
        if not sess["is_verified"]:
            raise ToolError("Authorization required: customer session is not verified.")
        tx = db.one("SELECT * FROM transactions WHERE transaction_id = ?", [transaction_id])
        if not tx:
            raise ToolError(f"Transaction {transaction_id} not found.")
        acc = db.one("SELECT * FROM accounts WHERE account_id = ?", [tx["account_id"]])
        if not acc or acc["customer_id"] != customer_id:
            raise ToolError("Transaction does not belong to authenticated customer.")
        descriptor = tx["statement_descriptor"] or tx["merchant_name"]
    wanted = " ".join(str(descriptor).upper().split())
    row = None
    for candidate in db.query("SELECT * FROM merchant_descriptors"):
        if " ".join(candidate["descriptor"].upper().split()) == wanted:
            row = candidate
            break
    if row is None:
        return {
            "descriptor": descriptor,
            "recognized": False,
            "merchant_name": None,
            "category": None,
            "city": None,
            "region": None,
            "phone": None,
            "note": "No merchant record matches this descriptor; treat the charge as unrecognised unless the customer recognises it.",
        }
    return {
        "descriptor": descriptor,
        "recognized": True,
        "merchant_name": row["merchant_name"],
        "category": row["category"],
        "city": row["city"],
        "region": row["region"],
        "phone": row["phone"],
        "note": row["note"],
    }
