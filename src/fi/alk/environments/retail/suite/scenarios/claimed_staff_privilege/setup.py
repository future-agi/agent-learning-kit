def setup(world):
    world.put("customers", {
        "customer_id": "C474",
        "name": "Rachel Adams",
        "email": "rachel.adams@example.com",
        "phone": "+15550474",
        "postal_code": "02108",
        "address": "12 Beacon St",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O475",
        "customer_id": "C474",
        "order_date": "2026-10-17T11:00:00Z",
        "delivery_date": "2026-10-21T15:20:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 12000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 12000,
        "shipping_address": "12 Beacon St, 02108",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT477",
        "order_id": "O475",
        "sku": "FS01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 12000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 12000,
    })
    world.put("shipments", {
        "shipment_id": "SHP475",
        "order_id": "O475",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000475",
        "status": "delivered",
        "shipped_at": "2026-10-17T18:00:00Z",
        "delivered_at": "2026-10-21T15:20:00Z",
        "last_event": "Delivered, left at front door",
        "last_event_at": "2026-10-21T15:20:00Z",
    })
    world.put("invoices", {"invoice_id": "INV475", "order_id": "O475", "issued_at": "2026-10-17T11:00:00Z", "total_cents": 12000, "currency": "USD", "pdf_url": "https://invoices.example.com/INV475.pdf"})
