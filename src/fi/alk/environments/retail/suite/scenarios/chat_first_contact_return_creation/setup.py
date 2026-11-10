def setup(world):
    world.put("customers", {
        "customer_id": "C440",
        "name": "Diana Prince",
        "email": "diana.prince@example.com",
        "phone": "+15550440",
        "postal_code": "90211",
        "address": "440 Amazon Way",
        "store_credit_cents": 0
    })
    world.put("orders", {
        "order_id": "O440",
        "customer_id": "C440",
        "order_date": "2026-10-18T10:00:00Z",
        "delivery_date": "2026-10-22T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 4800,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 4800,
        "shipping_address": "440 Amazon Way, 90211",
        "cancelled_at": None
    })
    world.put("order_items", {
        "item_id": "IT440",
        "order_id": "O440",
        "sku": "AP01",
        "quantity": 1,
        "unit_price_cents": 4800,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 4800,
        "returned_quantity": 0
    })
    world.put("shipments", {
        "shipment_id": "SHP440",
        "order_id": "O440",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000440",
        "status": "delivered",
        "shipped_at": "2026-10-18T18:00:00Z",
        "delivered_at": "2026-10-22T10:00:00Z",
        "last_event": "Delivered, front porch",
        "last_event_at": "2026-10-22T10:00:00Z"
    })
