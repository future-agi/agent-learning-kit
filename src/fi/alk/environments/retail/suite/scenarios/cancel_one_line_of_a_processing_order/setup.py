def setup(world):
    world.put("customers", {
        "customer_id": "C473",
        "name": "David Kim",
        "email": "david.kim@example.com",
        "phone": "+15550473",
        "postal_code": "90001",
        "address": "550 Grand Ave",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O474",
        "customer_id": "C473",
        "order_date": "2026-10-26T09:00:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 9800,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 9800,
        "shipping_address": "550 Grand Ave, 90001",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT475",
        "order_id": "O474",
        "sku": "SH01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 8000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 8000,
    })
    world.put("order_items", {
        "item_id": "IT476",
        "order_id": "O474",
        "sku": "AC13",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 1800,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 1800,
    })
    world.put("shipments", {
        "shipment_id": "SHP474",
        "order_id": "O474",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000474",
        "status": "created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T09:05:00Z",
    })
