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
    customer_id = args.get("customer_id")
    confirmed = args.get("confirmed")
    if not return_id or not customer_id:
        raise ToolError("return_id and customer_id are required.", category="invalid_input")
    if confirmed is None:
        raise ToolError("confirmed is required.", category="invalid_input")
    if not confirmed:
        raise ToolError("Policy: the customer must confirm the cancellation first.", category="policy_blocked")
    ret = db.one("SELECT * FROM returns WHERE return_id = ?", [return_id])
    if not ret:
        raise ToolError(f"Return {return_id} not found.", category="not_found")
    if ret["customer_id"].strip() != str(customer_id).strip():
        raise ToolError(f"Return {return_id} does not belong to customer {customer_id}.", category="permission_denied")
    _for_caller(db, customer_id, f"return {return_id}")
    clock = db.one("SELECT now FROM clock")["now"]
    if ret["status"] == "cancelled":
        return {"return_id": return_id, "order_id": ret["order_id"], "status": "cancelled", "idempotent": True, "cancelled_at": ret["cancelled_at"]}
    if ret["status"] != "authorized":
        raise ToolError(f"Policy: return {return_id} is {ret['status']} and can no longer be cancelled.", category="policy_blocked")
    for item in db.query("SELECT * FROM return_items WHERE return_id = ?", [return_id]):
        # Never below zero: a counter that lagged behind the return must not go negative and make
        # more units returnable than were bought.
        db.execute("UPDATE order_items SET returned_quantity = CASE WHEN returned_quantity > ? THEN returned_quantity - ? ELSE 0 END WHERE item_id = ?", [item["quantity"], item["quantity"], item["item_id"]])
    db.execute("UPDATE returns SET status = 'cancelled', cancelled_at = ? WHERE return_id = ?", [clock, return_id])
    db.execute("UPDATE return_labels SET status = 'void' WHERE return_id = ?", [return_id])
    return {"return_id": return_id, "order_id": ret["order_id"], "status": "cancelled", "idempotent": False, "cancelled_at": clock, "label_status": "void"}
