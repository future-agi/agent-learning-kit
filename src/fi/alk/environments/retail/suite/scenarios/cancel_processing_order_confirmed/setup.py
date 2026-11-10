def setup(world):
    world.put("customers", {
        "customer_id": "C430",
        "name": "Jordan Bell",
        "email": "jordan.bell@example.com",
        "phone": "+15550430",
        "postal_code": "98101",
        "address": "400 Pine St",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O430",
        "customer_id": "C430",
        "order_date": "2026-10-26T08:00:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 8000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 8000,
        "shipping_address": "400 Pine St, 98101",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT430",
        "order_id": "O430",
        "sku": "SH01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 8000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 8000,
    })
    world.put("shipments", {
        "shipment_id": "SHP430",
        "order_id": "O430",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000430",
        "status": "created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T08:05:00Z",
    })
