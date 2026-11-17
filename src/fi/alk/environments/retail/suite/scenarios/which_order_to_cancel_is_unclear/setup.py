def setup(world):
    world.put("customers", {
        "customer_id": "C470",
        "name": "Elena Rostova",
        "email": "elena.rostova@example.com",
        "phone": "+15550470",
        "postal_code": "94102",
        "address": "100 Market St",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O470",
        "customer_id": "C470",
        "order_date": "2026-10-26T08:00:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 8000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 8000,
        "shipping_address": "100 Market St, 94102",
        "cancelled_at": None,
    })
    world.put("orders", {
        "order_id": "O471",
        "customer_id": "C470",
        "order_date": "2026-10-26T08:30:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 2800,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 2800,
        "shipping_address": "100 Market St, 94102",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT470",
        "order_id": "O470",
        "sku": "SH01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 8000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 8000,
    })
    world.put("order_items", {
        "item_id": "IT471",
        "order_id": "O471",
        "sku": "AP01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 2800,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 2800,
    })
    world.put("shipments", {
        "shipment_id": "SHP470",
        "order_id": "O470",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000470",
        "status": "label_created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T08:05:00Z",
    })
    world.put("shipments", {
        "shipment_id": "SHP471",
        "order_id": "O471",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000471",
        "status": "label_created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T08:35:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV470",
        "order_id": "O470",
        "issued_at": "2026-10-26T08:00:00Z",
        "total_cents": 8000,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV470.pdf",
    })
    world.put("invoices", {
        "invoice_id": "INV471",
        "order_id": "O471",
        "issued_at": "2026-10-26T08:30:00Z",
        "total_cents": 2800,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV471.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C470"}, by="id")
