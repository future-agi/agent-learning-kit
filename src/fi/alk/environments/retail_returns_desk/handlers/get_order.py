def handle(args, db):
    order_id = args.get("order_id")
    if not order_id:
        raise ToolError("order_id is required.", category="invalid_input")
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
    items = db.query("SELECT * FROM order_items WHERE order_id = ?", [order_id])
    res_items = []
    for it in items:
        prod = db.one("SELECT * FROM products WHERE sku = ?", [it["sku"]])
        title = prod["title"] if prod else it["sku"]
        is_final_sale = prod["is_final_sale"] if prod else False
        returnable = prod["returnable"] if prod else True
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
        "items": res_items
    }
