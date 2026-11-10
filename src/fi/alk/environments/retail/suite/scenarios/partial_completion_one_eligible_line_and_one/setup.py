def setup(world):
    world.put("customers", {
        "customer_id": "C471",
        "name": "Marcus Vance",
        "email": "marcus.vance@example.com",
        "phone": "+15550471",
        "postal_code": "30301",
        "address": "78 Peachtree St",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O472",
        "customer_id": "C471",
        "order_date": "2026-10-18T10:00:00Z",
        "delivery_date": "2026-10-20T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 9400,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 9400,
        "shipping_address": "78 Peachtree St, 30301",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT472",
        "order_id": "O472",
        "sku": "AP01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 3400,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 3400,
    })
    world.put("order_items", {
        "item_id": "IT473",
        "order_id": "O472",
        "sku": "FS02",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 6000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 6000,
    })
    world.put("shipments", {
        "shipment_id": "SHP472",
        "order_id": "O472",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000472",
        "status": "delivered",
        "shipped_at": "2026-10-18T18:00:00Z",
        "delivered_at": "2026-10-20T10:00:00Z",
        "last_event": "Delivered, left at front door",
        "last_event_at": "2026-10-20T10:00:00Z",
    })
