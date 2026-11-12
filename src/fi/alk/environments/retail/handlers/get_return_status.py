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


def handle(args, db):
    return_id = args.get("return_id")
    order_id = args.get("order_id")
    
    if not return_id and not order_id:
        raise ToolError("Either return_id or order_id must be provided.", category="invalid_input")
        
    if return_id and order_id:
        rows = db.query("SELECT * FROM returns WHERE return_id = ? AND order_id = ?", [return_id, order_id])
    elif return_id:
        rows = db.query("SELECT * FROM returns WHERE return_id = ?", [return_id])
    else:
        rows = db.query("SELECT * FROM returns WHERE order_id = ?", [order_id])
        
    if order_id:
        order = db.one("SELECT customer_id FROM orders WHERE order_id = ?", [order_id])
        if order:
            _for_caller(db, order["customer_id"], f"order {order_id}")
    # Only the signed-in customer's returns: a return number is no proof of whose it is.
    for r in rows:
        _for_caller(db, r["customer_id"], f"return {r['return_id']}")
    if not rows:
        raise ToolError(f"No return records found for return_id={return_id!r}, order_id={order_id!r}.", category="not_found")
        
    res_list = []
    for r in rows:
        items = db.query("SELECT * FROM return_items WHERE return_id = ?", [r["return_id"]])
        lbl = db.one("SELECT * FROM return_labels WHERE return_id = ?", [r["return_id"]])
        
        lbl_obj = None
        lbl_status = "unavailable"
        if lbl:
            lbl_obj = {
                "label_id": lbl["label_id"],
                "tracking_number": lbl["tracking_number"],
                "carrier": lbl["carrier"],
                "label_url": lbl["label_url"],
                "status": lbl["status"]
            }
            lbl_status = lbl["status"]
            
        res_list.append({
            "return_id": r["return_id"],
            "order_id": r["order_id"],
            "customer_id": r["customer_id"],
            "created_at": r["created_at"],
            "status": r["status"],
            "estimated_refund_cents": r["estimated_refund_cents"],
            "restocking_fee_cents": r["restocking_fee_cents"],
            "return_reason": r["return_reason"],
            "items": [
                {
                    "return_item_id": it["return_item_id"],
                    "item_id": it["item_id"],
                    "sku": it["sku"],
                    "quantity": it["quantity"],
                    "refunded_amount_cents": it["refunded_amount_cents"]
                }
                for it in items
            ],
            "label": lbl_obj,
            "label_status": lbl_status
        })
        
    return {"returns": res_list}
