def setup(world):
    world.put("customers", {
        "customer_id": "C460",
        "name": "David Miller",
        "email": "david.miller@example.com",
        "postal_code": "M5V 2T6",
        "address": "100 King St W, Toronto, ON",
        "phone": "+15550460",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O460",
        "customer_id": "C460",
        "order_date": "2026-10-16T10:00:00Z",
        "delivery_date": "2026-10-20T10:00:00Z",
        "status": "delivered",
        "shipping_address": "100 King St W, Toronto, ON M5V 2T6",
        "total_gross_cents": 8000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 8000,
        "currency": "USD",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT460",
        "order_id": "O460",
        "sku": "SH01",
        "quantity": 1,
        "unit_price_cents": 8000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 8000,
        "returned_quantity": 0,
    })
    world.put("shipments", {
        "shipment_id": "SHP460",
        "order_id": "O460",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000460",
        "status": "delivered",
        "shipped_at": "2026-10-16T18:00:00Z",
        "delivered_at": "2026-10-20T10:00:00Z",
        "last_event": "Delivered, left at front door",
        "last_event_at": "2026-10-20T10:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV460",
        "order_id": "O460",
        "issued_at": "2026-10-16T10:00:00Z",
        "total_cents": 8000,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV460.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C460"}, by="id")
