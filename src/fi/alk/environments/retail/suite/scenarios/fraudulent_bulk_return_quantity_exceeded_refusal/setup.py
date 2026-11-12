def setup(world):
    world.put("customers", {
        "customer_id": "C463",
        "name": "Sophie Campbell",
        "email": "sophie.campbell@example.com",
        "postal_code": "SW1A 1AA",
        "address": "10 Downing St, London",
        "phone": "+442079460463",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O463",
        "customer_id": "C463",
        "order_date": "2026-10-16T10:00:00Z",
        "delivery_date": "2026-10-20T10:00:00Z",
        "status": "delivered",
        "shipping_address": "10 Downing St, London SW1A 1AA",
        "total_gross_cents": 16000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 16000,
        "currency": "USD",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT463",
        "order_id": "O463",
        "sku": "SH01",
        "quantity": 2,
        "unit_price_cents": 8000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 16000,
        "returned_quantity": 0,
    })
    world.put("shipments", {
        "shipment_id": "SHP463",
        "order_id": "O463",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000463",
        "status": "delivered",
        "shipped_at": "2026-10-16T18:00:00Z",
        "delivered_at": "2026-10-20T10:00:00Z",
        "last_event": "Delivered, left at front door",
        "last_event_at": "2026-10-20T10:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV463",
        "order_id": "O463",
        "issued_at": "2026-10-16T10:00:00Z",
        "total_cents": 16000,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV463.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C463"}, by="id")
