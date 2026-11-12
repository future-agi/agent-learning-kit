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


import json
from datetime import datetime

def handle(args, db):
    order_id = args.get("order_id")
    customer_id = args.get("customer_id")
    original_sku = args.get("original_sku")
    replacement_sku = args.get("replacement_sku")
    quantity = args.get("quantity")
    confirmed = args.get("confirmed")
    request_key = args.get("request_key")
    if not order_id or not customer_id or not original_sku or not replacement_sku:
        raise ToolError("order_id, customer_id, original_sku and replacement_sku are required.", category="invalid_input")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ToolError("quantity must be a positive integer.", category="invalid_input")
    if confirmed is None:
        raise ToolError("confirmed is required.", category="invalid_input")
    if not confirmed:
        raise ToolError("Policy: the customer must confirm the exchange first.", category="policy_blocked")
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
    if order["customer_id"].strip() != str(customer_id).strip():
        raise ToolError(f"Customer {customer_id} does not match order owner.", category="permission_denied")
    _for_caller(db, customer_id, f"order {order_id}")
    if order["status"] != "delivered" or not order["delivery_date"]:
        raise ToolError(f"Cannot exchange items from an order with status '{order['status']}'.", category="policy_blocked")
    clock = db.one("SELECT now FROM clock")["now"]
    if request_key:
        existing = db.one("SELECT * FROM exchanges WHERE request_key = ?", [request_key])
        if existing:
            # A replay is the same exchange by the same customer; anything else under the key is a
            # conflict, never another exchange reported as this one.
            if (existing["order_id"], existing["customer_id"], existing["original_sku"], existing["replacement_sku"], existing["quantity"]) != (order_id, customer_id, original_sku, replacement_sku, quantity):
                raise ToolError(f"request_key {request_key!r} was already used for a different exchange.", category="conflict")
            return {"exchange_id": existing["exchange_id"], "order_id": order_id, "original_sku": existing["original_sku"], "replacement_sku": existing["replacement_sku"], "quantity": existing["quantity"], "price_difference_cents": existing["price_difference_cents"], "status": existing["status"], "idempotent": True}
    line = db.one("SELECT * FROM order_items WHERE order_id = ? AND sku = ?", [order_id, original_sku])
    if not line:
        raise ToolError(f"SKU {original_sku} is not part of order {order_id}.", category="not_found")
    original = db.one("SELECT * FROM products WHERE sku = ?", [original_sku])
    replacement = db.one("SELECT * FROM products WHERE sku = ?", [replacement_sku])
    if not replacement:
        raise ToolError(f"Product {replacement_sku} not found.", category="not_found")
    policy = {r["name"]: json.loads(r["value"]) for r in db.query("SELECT name, value FROM environment_policy")}
    if original and original["is_final_sale"] and bool(policy.get("final_sale_enforced", True)):
        raise ToolError(f"SKU {original_sku} is final sale and cannot be exchanged.", category="policy_blocked")
    if original and not original["returnable"] and not (original["is_final_sale"] and not bool(policy.get("final_sale_enforced", True))):
        raise ToolError(f"SKU {original_sku} is not returnable, so it cannot be exchanged.", category="policy_blocked")
    if original and replacement["category"] != original["category"]:
        raise ToolError(f"Policy: an exchange stays within the category; {replacement_sku} is {replacement['category']}, {original_sku} is {original['category']}.", category="policy_blocked")
    window = int(policy.get("exchange_window_days", 30))
    if original and original["category"] == "Accessories":
        window = min(window, int(policy.get("accessories_return_window_days", 14)))
    delivered = datetime.fromisoformat(order["delivery_date"].replace("Z", "+00:00"))
    now = datetime.fromisoformat(clock.replace("Z", "+00:00"))
    if (now - delivered).days > window:
        raise ToolError(f"Policy: order {order_id} was delivered on {order['delivery_date'][:10]}, outside the {window}-day exchange window.", category="policy_blocked")
    # Units on an active return are not held any more, even where the line's counter lags behind.
    on_returns = db.one(
        "SELECT COALESCE(SUM(ri.quantity), 0) AS q FROM return_items ri JOIN returns r ON r.return_id = ri.return_id "
        "WHERE ri.item_id = ? AND r.status != 'cancelled'",
        [line["item_id"]],
    )
    available = line["quantity"] - max(line["returned_quantity"], int(on_returns["q"] if on_returns else 0))
    if quantity > available:
        raise ToolError(f"Requested quantity {quantity} exceeds the {available} unit(s) still held for SKU {original_sku}.", category="conflict")
    if db.one("SELECT 1 FROM exchanges WHERE order_id = ? AND original_sku = ? AND status != 'cancelled'", [order_id, original_sku]):
        raise ToolError(f"Policy: one exchange per order line; {original_sku} on {order_id} was already exchanged.", category="conflict")
    unit_net = int(round(line["line_net_cents"] / line["quantity"]))
    difference = (replacement["list_price_cents"] - unit_net) * quantity
    count = db.one("SELECT COUNT(*) AS c FROM exchanges")["c"]
    exchange_id = f"EX{count + 1:02d}"
    db.execute("INSERT INTO exchanges (exchange_id, order_id, customer_id, original_sku, replacement_sku, quantity, price_difference_cents, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [exchange_id, order_id, customer_id, original_sku, replacement_sku, quantity, difference, "authorized", request_key, clock])
    db.execute("UPDATE order_items SET returned_quantity = returned_quantity + ? WHERE item_id = ?", [quantity, line["item_id"]])
    if difference < 0:
        credit_count = db.one("SELECT COUNT(*) AS c FROM store_credits")["c"]
        db.execute("INSERT INTO store_credits (credit_id, customer_id, amount_cents, source, issued_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)", [f"SCR{credit_count + 1:02d}", customer_id, -difference, f"price difference on exchange {exchange_id}", clock, clock[:4].replace(clock[:4], str(int(clock[:4]) + 1)) + clock[4:]])
        db.execute("UPDATE customers SET store_credit_cents = store_credit_cents + ? WHERE customer_id = ?", [-difference, customer_id])
    return {"exchange_id": exchange_id, "order_id": order_id, "original_sku": original_sku, "replacement_sku": replacement_sku, "quantity": quantity, "price_difference_cents": difference, "difference_note": ("charged before the replacement ships" if difference > 0 else "issued as store credit" if difference < 0 else "no difference"), "status": "authorized", "idempotent": False}
