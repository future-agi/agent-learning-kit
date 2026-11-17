import json
from datetime import datetime

def handle(args, db):
    order_id = args.get("order_id")
    customer_id = args.get("customer_id")
    items = args.get("items")
    
    if not order_id:
        raise ToolError("order_id is required.", category="invalid_input")
    if not customer_id:
        raise ToolError("customer_id is required.", category="invalid_input")
    if not isinstance(items, list) or len(items) == 0:
        raise ToolError("items list must be provided.", category="invalid_input")
        
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
        
    if order["customer_id"].strip() != customer_id.strip():
        raise ToolError(f"Customer {customer_id} does not match order owner.", category="permission_denied")
        
    if order["status"] != "delivered" or not order["delivery_date"]:
        raise ToolError(f"Cannot return items from order with status '{order['status']}'.", category="policy_blocked")
        
    clock_row = db.one("SELECT now FROM clock")
    now_dt = datetime.fromisoformat(clock_row["now"].replace("Z", "+00:00"))
    
    policy_rows = db.query("SELECT name, value FROM environment_policy")
    policy_map = {row["name"]: json.loads(row["value"]) for row in policy_rows}
    window_days = policy_map.get("return_window_days", 30)
    fee_rate = float(policy_map.get("restocking_fee_rate", 0.0))
    final_sale_enforced = bool(policy_map.get("final_sale_enforced", True))
    label_avail = bool(policy_map.get("label_service_available", True))
    
    delivery_dt = datetime.fromisoformat(order["delivery_date"].replace("Z", "+00:00"))
    if (now_dt - delivery_dt).days > window_days:
        raise ToolError(f"Order {order_id} exceeds return window of {window_days} days.", category="policy_blocked")
        
    validated_items = []
    combined_reason = []
    for req in items:
        sku = req.get("sku")
        qty = req.get("quantity")
        reason = req.get("reason", "customer_request")
        if not sku:
            raise ToolError("Each item must have a sku.", category="invalid_input")
        if qty is None or not isinstance(qty, int) or qty <= 0:
            raise ToolError(f"Invalid quantity {qty} for SKU {sku}.", category="invalid_input")
            
        ord_item = db.one("SELECT * FROM order_items WHERE order_id = ? AND sku = ?", [order_id, sku])
        if not ord_item:
            raise ToolError(f"SKU {sku} is not part of order {order_id}.", category="not_found")
            
        prod = db.one("SELECT * FROM products WHERE sku = ?", [sku])
        if prod and prod["is_final_sale"] and final_sale_enforced:
            raise ToolError(f"SKU {sku} is final sale and cannot be returned.", category="policy_blocked")
            
        avail_qty = ord_item["quantity"] - ord_item["returned_quantity"]
        if qty > avail_qty:
            raise ToolError(f"Requested quantity {qty} exceeds available returnable quantity {avail_qty} for SKU {sku}.", category="conflict")
            
        unit_net = int(round(ord_item["line_net_cents"] / ord_item["quantity"]))
        item_gross = unit_net * qty
        item_fee = int(round(item_gross * fee_rate))
        item_net = item_gross - item_fee
        
        validated_items.append({
            "item_id": ord_item["item_id"],
            "sku": sku,
            "quantity": qty,
            "reason": reason,
            "unit_net_cents": unit_net,
            "gross_refund_cents": item_gross,
            "restocking_fee_cents": item_fee,
            "refunded_amount_cents": item_net
        })
        combined_reason.append(f"{sku}:{reason}")
        
    total_fee = sum(it["restocking_fee_cents"] for it in validated_items)
    total_net = sum(it["refunded_amount_cents"] for it in validated_items)
    
    ret_count = db.one("SELECT COUNT(*) as count FROM returns")["count"]
    return_id = f"R{ret_count + 1:02d}"
    while db.one("SELECT * FROM returns WHERE return_id = ?", [return_id]):
        ret_count += 1
        return_id = f"R{ret_count + 1:02d}"
        
    created_at = clock_row["now"]
    db.execute(
        "INSERT INTO returns (return_id, order_id, customer_id, created_at, status, estimated_refund_cents, restocking_fee_cents, return_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [return_id, order_id, customer_id, created_at, "authorized", total_net, total_fee, "; ".join(combined_reason)]
    )
    
    res_items = []
    for it in validated_items:
        ri_count = db.one("SELECT COUNT(*) as count FROM return_items")["count"]
        ri_id = f"RI{ri_count + 1:02d}"
        while db.one("SELECT * FROM return_items WHERE return_item_id = ?", [ri_id]):
            ri_count += 1
            ri_id = f"RI{ri_count + 1:02d}"
            
        db.execute(
            "INSERT INTO return_items (return_item_id, return_id, item_id, sku, quantity, refunded_amount_cents) VALUES (?, ?, ?, ?, ?, ?)",
            [ri_id, return_id, it["item_id"], it["sku"], it["quantity"], it["refunded_amount_cents"]]
        )
        db.execute(
            "UPDATE order_items SET returned_quantity = returned_quantity + ? WHERE item_id = ?",
            [it["quantity"], it["item_id"]]
        )
        res_items.append({
            "sku": it["sku"],
            "quantity": it["quantity"],
            "refunded_amount_cents": it["refunded_amount_cents"]
        })
        
    label_obj = None
    label_status = "unavailable"
    if label_avail:
        lbl_count = db.one("SELECT COUNT(*) as count FROM return_labels")["count"]
        label_id = f"L{lbl_count + 1:02d}"
        while db.one("SELECT * FROM return_labels WHERE label_id = ?", [label_id]):
            lbl_count += 1
            label_id = f"L{lbl_count + 1:02d}"
            
        tracking = f"940011189922319{lbl_count + 1:06d}"
        carrier = "USPS"
        url = f"https://labels.example.com/{label_id}.pdf"
        
        db.execute(
            "INSERT INTO return_labels (label_id, return_id, tracking_number, carrier, label_url, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [label_id, return_id, tracking, carrier, url, "active", created_at]
        )
        label_obj = {
            "label_id": label_id,
            "tracking_number": tracking,
            "carrier": carrier,
            "label_url": url,
            "status": "active"
        }
        label_status = "active"
        
    return {
        "return_id": return_id,
        "order_id": order_id,
        "customer_id": customer_id,
        "status": "authorized",
        "estimated_refund_cents": total_net,
        "restocking_fee_cents": total_fee,
        "label": label_obj,
        "label_status": label_status,
        "items": res_items
    }
