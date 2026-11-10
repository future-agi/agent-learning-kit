def setup(world):
    world.put("customers", {
        "customer_id": "C452",
        "name": "Stephen Strange",
        "email": "stephen.strange@example.com",
        "phone": "+15550452",
        "postal_code": "10012",
        "address": "177A Bleecker Street",
        "store_credit_cents": 0
    })
    world.put("orders", {
        "order_id": "O452",
        "customer_id": "C452",
        "order_date": "2026-10-14T10:00:00Z",
        "delivery_date": "2026-10-18T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 3500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 3500,
        "shipping_address": "177A Bleecker Street, 10012",
        "cancelled_at": None
    })
    world.put("order_items", {
        "item_id": "IT452",
        "order_id": "O452",
        "sku": "AP06",
        "quantity": 1,
        "unit_price_cents": 3500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 3500,
        "returned_quantity": 1
    })
    world.put("returns", {
        "return_id": "R451",
        "order_id": "O452",
        "customer_id": "C452",
        "status": "authorized",
        "return_reason": "AP06:changed_mind",
        "estimated_refund_cents": 3500,
        "restocking_fee_cents": 0,
        "request_key": "REQ-C452-O452",
        "created_at": "2026-10-22T10:00:00Z",
        "cancelled_at": None
    })
    world.put("return_items", {
        "return_item_id": "RI451",
        "return_id": "R451",
        "item_id": "IT452",
        "sku": "AP06",
        "quantity": 1,
        "refunded_amount_cents": 3500
    })
    world.put("return_labels", {
        "label_id": "L451",
        "return_id": "R451",
        "tracking_number": "9400111899223190000451",
        "carrier": "USPS",
        "label_url": "https://labels.example.com/L451.pdf",
        "status": "active",
        "created_at": "2026-10-22T10:00:00Z"
    })
