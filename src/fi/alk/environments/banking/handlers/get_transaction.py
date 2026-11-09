def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    transaction_id = args.get("transaction_id")
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if not transaction_id:
        raise ToolError("Missing transaction_id.")
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
    card = db.one("SELECT last_four FROM cards WHERE card_id = ?", [tx["card_id"]])
    dispute = db.one("SELECT dispute_id, status FROM disputes WHERE transaction_id = ?", [transaction_id])
    return {
        "transaction_id": tx["transaction_id"],
        "account_id": tx["account_id"],
        "card_id": tx["card_id"],
        "card_last_four": card["last_four"] if card else "",
        "merchant_name": tx["merchant_name"],
        "statement_descriptor": tx["statement_descriptor"] or tx["merchant_name"],
        "amount": tx["amount"],
        "currency": tx["currency"],
        "status": tx["status"],
        "transaction_time": tx["transaction_time"],
        "description": tx["description"],
        "dispute_id": dispute["dispute_id"] if dispute else None,
        "dispute_status": dispute["status"] if dispute else None,
    }
