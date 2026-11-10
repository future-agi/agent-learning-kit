def setup(world):
    world.put("customers", {
        "customer_id": "C420",
        "name": "Liam Evans",
        "email": "liam.evans@example.com",
        "phone": "+15550420",
        "postal_code": "94102",
        "address": "450 Market St, San Francisco, CA 94102",
        "store_credit_cents": 0
    })
    world.put("orders", {
        "order_id": "O420",
        "customer_id": "C420",
        "order_date": "2026-10-18T10:00:00Z",
        "delivery_date": "2026-10-23T14:00:00Z",
        "status": "delivered",
        "total_gross_cents": 8000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 8000,
        "currency": "USD",
        "shipping_address": "450 Market St, San Francisco, CA 94102",
        "cancelled_at": None
    })
    world.put("order_items", {
        "item_id": "IT420",
        "order_id": "O420",
        "sku": "SH01",
        "quantity": 1,
        "unit_price_cents": 8000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 8000,
        "returned_quantity": 0
    })
    world.put("shipments", {
        "shipment_id": "SHP420",
        "order_id": "O420",
        "tracking_number": "1Z999AA10000000420",
        "carrier": "UPS",
        "status": "delivered",
        "shipped_at": "2026-10-19T08:00:00Z",
        "delivered_at": "2026-10-23T14:00:00Z",
        "last_event": "Delivered, left at front door",
        "last_event_at": "2026-10-23T14:00:00Z"
    })
