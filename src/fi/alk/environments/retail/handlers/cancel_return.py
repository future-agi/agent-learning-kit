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
    clock = db.one("SELECT now FROM clock")["now"]
    if ret["status"] == "cancelled":
        return {"return_id": return_id, "order_id": ret["order_id"], "status": "cancelled", "idempotent": True, "cancelled_at": ret["cancelled_at"]}
    if ret["status"] != "authorized":
        raise ToolError(f"Policy: return {return_id} is {ret['status']} and can no longer be cancelled.", category="policy_blocked")
    for item in db.query("SELECT * FROM return_items WHERE return_id = ?", [return_id]):
        db.execute("UPDATE order_items SET returned_quantity = returned_quantity - ? WHERE item_id = ?", [item["quantity"], item["item_id"]])
    db.execute("UPDATE returns SET status = 'cancelled', cancelled_at = ? WHERE return_id = ?", [clock, return_id])
    db.execute("UPDATE return_labels SET status = 'void' WHERE return_id = ?", [return_id])
    return {"return_id": return_id, "order_id": ret["order_id"], "status": "cancelled", "idempotent": False, "cancelled_at": clock, "label_status": "void"}
