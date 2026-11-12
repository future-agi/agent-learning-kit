def setup(world):
    world.put("customers", {
        "customer_id": "C450",
        "name": "Clint Barton",
        "email": "clint.barton@example.com",
        "phone": "+15550450",
        "postal_code": "50001",
        "address": "450 Hawkeye Way",
        "store_credit_cents": 0
    })
    world.put("orders", {
        "order_id": "O450",
        "customer_id": "C450",
        "order_date": "2026-10-15T10:00:00Z",
        "delivery_date": "2026-10-20T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 9800,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 9800,
        "shipping_address": "450 Hawkeye Way, 50001",
        "cancelled_at": None
    })
    world.put("order_items", {
        "item_id": "IT450",
        "order_id": "O450",
        "sku": "AP05",
        "quantity": 1,
        "unit_price_cents": 9800,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 9800,
        "returned_quantity": 0
    })
    world.put("shipments", {
        "shipment_id": "SHP450",
        "order_id": "O450",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000450",
        "status": "delivered",
        "shipped_at": "2026-10-15T18:00:00Z",
        "delivered_at": "2026-10-20T10:00:00Z",
        "last_event": "Delivered, front porch",
        "last_event_at": "2026-10-20T10:00:00Z"
    })
    world.put("invoices", {"invoice_id": "INV450", "order_id": "O450", "issued_at": "2026-10-15T10:00:00Z", "total_cents": 9800, "currency": "USD", "pdf_url": "https://invoices.example.com/INV450.pdf"})
