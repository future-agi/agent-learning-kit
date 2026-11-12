def setup(world):
    world.put("customers", {
        "customer_id": "C413",
        "name": "Barry Allen",
        "email": "barry.allen@example.com",
        "phone": "+15550413",
        "postal_code": "64101",
        "address": "404 Central Ave, Kansas City, MO 64101",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O413",
        "customer_id": "C413",
        "order_date": "2026-10-18T10:00:00Z",
        "delivery_date": "2026-10-22T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 5200,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 5200,
        "shipping_address": "404 Central Ave, Kansas City, MO 64101",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT413",
        "order_id": "O413",
        "sku": "AP08",
        "quantity": 1,
        "unit_price_cents": 5200,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 5200,
        "returned_quantity": 1,
    })
    world.put("shipments", {
        "shipment_id": "SHP413",
        "order_id": "O413",
        "tracking_number": "1Z999AA1000000413",
        "carrier": "UPS",
        "status": "delivered",
        "shipped_at": "2026-10-19T10:00:00Z",
        "delivered_at": "2026-10-22T10:00:00Z",
        "last_event": "Delivered",
        "last_event_at": "2026-10-22T10:00:00Z",
    })
    world.put("exchanges", {
        "exchange_id": "EXC413",
        "order_id": "O413",
        "customer_id": "C413",
        "original_sku": "AP08",
        "replacement_sku": "AP08",
        "quantity": 1,
        "price_difference_cents": 0,
        "status": "completed",
        "request_key": "REQ-EXC-PREV-413",
        "created_at": "2026-10-24T10:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV413",
        "order_id": "O413",
        "issued_at": "2026-10-18T10:00:00Z",
        "total_cents": 5200,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV413.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C413"}, by="id")
