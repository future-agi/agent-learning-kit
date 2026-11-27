def setup(world):
    world.put("customers", {
        "customer_id": "C432",
        "name": "David Kim",
        "email": "david.kim@example.com",
        "phone": "+15550432",
        "postal_code": "78701",
        "address": "100 Congress Ave",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O432",
        "customer_id": "C432",
        "order_date": "2026-10-26T08:30:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 12000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 12000,
        "shipping_address": "100 Congress Ave, 78701",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT432",
        "order_id": "O432",
        "sku": "SH03",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 12000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 12000,
    })
    world.put("shipments", {
        "shipment_id": "SHP432",
        "order_id": "O432",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000432",
        "status": "label_created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T08:35:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV432",
        "order_id": "O432",
        "issued_at": "2026-10-26T08:30:00Z",
        "total_cents": 12000,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV432.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C432"}, by="id")
