def _caller(db):
    """The customer signed in to this conversation. The session sets it; an ID, email or name the
    desk is told never changes who it is talking to."""
    row = db.one("SELECT customer_id FROM session_caller WHERE id = 'default'")
    return row["customer_id"] if row else None


def handle(args, db):
    order_id = args.get("order_id")
    if not order_id:
        raise ToolError("order_id is required.", category="invalid_input")
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order or order["customer_id"] != _caller(db):
        # Another customer's order is answered like a missing one: its existence is theirs too.
        raise ToolError(f"Order {order_id} is not an order of the customer signed in to this conversation.", category="permission_denied" if order else "not_found")
    items = db.query("SELECT * FROM order_items WHERE order_id = ?", [order_id])
    exchanges = db.query("SELECT * FROM exchanges WHERE order_id = ? AND status != 'cancelled' ORDER BY created_at", [order_id])
    res_items = []
    for it in items:
        prod = db.one("SELECT * FROM products WHERE sku = ?", [it["sku"]])
        title = prod["title"] if prod else it["sku"]
        is_final_sale = prod["is_final_sale"] if prod else False
        returnable = prod["returnable"] if prod else True
        exchanged = sum(int(e["quantity"]) for e in exchanges if e["original_sku"] == it["sku"])
        res_items.append({
            "item_id": it["item_id"],
            "sku": it["sku"],
            "title": title,
            "quantity": it["quantity"],
            "unit_price_cents": it["unit_price_cents"],
            "line_discount_cents": it["line_discount_cents"],
            "line_tax_cents": it["line_tax_cents"],
            "line_net_cents": it["line_net_cents"],
            "returned_quantity": it["returned_quantity"],
            # Units already exchanged count as returned; shown apart because a line is exchanged once.
            "exchanged_quantity": exchanged,
            "returnable_quantity": max(0, it["quantity"] - it["returned_quantity"]),
            "is_final_sale": bool(is_final_sale),
            "returnable": bool(returnable)
        })
    return {
        "order_id": order["order_id"],
        "customer_id": order["customer_id"],
        "order_date": order["order_date"],
        "delivery_date": order["delivery_date"],
        "status": order["status"],
        "currency": order["currency"],
        "total_gross_cents": order["total_gross_cents"],
        "total_discount_cents": order["total_discount_cents"],
        "total_tax_cents": order["total_tax_cents"],
        "total_net_cents": order["total_net_cents"],
        "items": res_items,
        "exchanges": [
            {"exchange_id": e["exchange_id"], "original_sku": e["original_sku"], "replacement_sku": e["replacement_sku"], "quantity": e["quantity"], "status": e["status"], "created_at": e["created_at"]}
            for e in exchanges
        ],
    }
