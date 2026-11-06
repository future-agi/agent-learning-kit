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
