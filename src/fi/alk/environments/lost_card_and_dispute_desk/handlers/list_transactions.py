def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    card_id = args.get("card_id")
    status = args.get("status")
    limit = args.get("limit")
    
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
        
    sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    if not sess:
        raise ToolError(f"Session {session_id} not found.")
    if sess["customer_id"] != customer_id:
        raise ToolError("Session customer mismatch.")
    if not sess["is_verified"]:
        raise ToolError("Authorization required: customer session is not verified.")
        
    if card_id:
        card = db.one("SELECT * FROM cards WHERE card_id = ? AND customer_id = ?", [card_id, customer_id])
        if not card:
            raise ToolError(f"Card {card_id} not found for customer {customer_id}.")
            
    sql = """
        SELECT t.transaction_id, t.account_id, t.card_id, t.merchant_name, t.amount, t.currency, t.status, t.transaction_time, t.description
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE a.customer_id = ?
    """
    params = [customer_id]
    
    if card_id:
        sql += " AND t.card_id = ?"
        params.append(card_id)
    if status:
        sql += " AND t.status = ?"
        params.append(status)
        
    sql += " ORDER BY t.transaction_time DESC"
    
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
        
    txs = db.query(sql, params)
    return {
        "customer_id": customer_id,
        "transactions": txs,
        "count": len(txs)
    }
