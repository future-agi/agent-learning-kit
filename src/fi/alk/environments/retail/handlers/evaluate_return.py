import json
from datetime import datetime

def handle(args, db):
    order_id = args.get("order_id")
    if not order_id:
        raise ToolError("order_id is required.", category="invalid_input")
    items = args.get("items")
    if not isinstance(items, list) or len(items) == 0:
        raise ToolError("items list must be provided.", category="invalid_input")
        
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
        
    clock_row = db.one("SELECT now FROM clock")
    now_dt = datetime.fromisoformat(clock_row["now"].replace("Z", "+00:00"))
    
    policy_rows = db.query("SELECT name, value FROM environment_policy")
    policy_map = {row["name"]: json.loads(row["value"]) for row in policy_rows}
    window_days = policy_map.get("return_window_days", 30)
    fee_rate = float(policy_map.get("restocking_fee_rate", 0.0))
    final_sale_enforced = bool(policy_map.get("final_sale_enforced", True))
    
    is_delivered = (order["status"] == "delivered" and order["delivery_date"] is not None)
    out_of_window = False
    if is_delivered:
        delivery_dt = datetime.fromisoformat(order["delivery_date"].replace("Z", "+00:00"))
        if (now_dt - delivery_dt).days > window_days:
            out_of_window = True
            
    items_eval = []
    for req in items:
        sku = req.get("sku")
        qty = req.get("quantity")
        reason = req.get("reason")
        if not sku:
            raise ToolError("Each item must have a sku.", category="invalid_input")
        if qty is None or not isinstance(qty, int) or qty <= 0:
            raise ToolError(f"Invalid quantity {qty} for SKU {sku}.", category="invalid_input")
            
        ord_item = db.one("SELECT * FROM order_items WHERE order_id = ? AND sku = ?", [order_id, sku])
        prod = db.one("SELECT * FROM products WHERE sku = ?", [sku])
        
        eligible = True
        ineligibility_reason = None
        
        if not ord_item:
            eligible = False
            ineligibility_reason = f"SKU {sku} is not part of order {order_id}."
            unit_net = 0
        elif not is_delivered:
            eligible = False
            ineligibility_reason = f"Order {order_id} is not yet delivered (current status: {order['status']})."
            unit_net = int(round(ord_item["line_net_cents"] / ord_item["quantity"]))
        elif out_of_window:
            eligible = False
            ineligibility_reason = f"Order {order_id} was delivered on {order['delivery_date']}, exceeding the {window_days}-day return window."
            unit_net = int(round(ord_item["line_net_cents"] / ord_item["quantity"]))
        elif prod and prod["is_final_sale"] and final_sale_enforced:
            eligible = False
            ineligibility_reason = f"SKU {sku} is marked as final sale and cannot be returned."
            unit_net = int(round(ord_item["line_net_cents"] / ord_item["quantity"]))
        else:
            unit_net = int(round(ord_item["line_net_cents"] / ord_item["quantity"]))
            returnable_qty = max(0, ord_item["quantity"] - ord_item["returned_quantity"])
            if returnable_qty == 0:
                eligible = False
                ineligibility_reason = f"All purchased units of SKU {sku} have already been returned."
            elif qty > returnable_qty:
                eligible = False
                ineligibility_reason = f"Requested quantity {qty} exceeds available returnable quantity {returnable_qty}."
                
        gross_refund = (unit_net * qty) if eligible else 0
        fee = int(round(gross_refund * fee_rate)) if eligible else 0
        net_refund = gross_refund - fee
        
        items_eval.append({
            "sku": sku,
            "quantity": qty,
            "reason": reason,
            "eligible": eligible,
            "ineligibility_reason": ineligibility_reason,
            "unit_net_cents": unit_net,
            "gross_refund_cents": gross_refund,
            "restocking_fee_cents": fee,
            "net_refund_cents": net_refund
        })
        
    overall_eligible = all(it["eligible"] for it in items_eval)
    tot_refund = sum(it["net_refund_cents"] for it in items_eval if it["eligible"])
    tot_fee = sum(it["restocking_fee_cents"] for it in items_eval if it["eligible"])
    
    return {
        "order_id": order_id,
        "overall_eligible": overall_eligible,
        "total_estimated_refund_cents": tot_refund,
        "total_restocking_fee_cents": tot_fee,
        "items_evaluation": items_eval
    }
