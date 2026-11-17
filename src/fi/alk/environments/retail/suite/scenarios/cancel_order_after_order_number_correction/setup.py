def setup(world):
    world.put("customers", {
        "customer_id": "C431",
        "name": "Priya Sharma",
        "email": "priya.sharma@example.com",
        "phone": "+15550431",
        "postal_code": "60602",
        "address": "120 S State St",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O431",
        "customer_id": "C431",
        "order_date": "2026-10-26T09:00:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 9500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 9500,
        "shipping_address": "120 S State St, 60602",
        "cancelled_at": None,
    })
    world.put("orders", {
        "order_id": "O435",
        "customer_id": "C431",
        "order_date": "2026-10-26T09:30:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 4500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 4500,
        "shipping_address": "120 S State St, 60602",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT431",
        "order_id": "O431",
        "sku": "SH02",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 9500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 9500,
    })
    world.put("order_items", {
        "item_id": "IT435",
        "order_id": "O435",
        "sku": "AC01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 4500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 4500,
    })
    world.put("shipments", {
        "shipment_id": "SHP431",
        "order_id": "O431",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000431",
        "status": "label_created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T09:05:00Z",
    })
    world.put("shipments", {
        "shipment_id": "SHP435",
        "order_id": "O435",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000435",
        "status": "label_created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T09:35:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV431",
        "order_id": "O431",
        "issued_at": "2026-10-26T09:00:00Z",
        "total_cents": 9500,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV431.pdf",
    })
    world.put("invoices", {
        "invoice_id": "INV435",
        "order_id": "O435",
        "issued_at": "2026-10-26T09:30:00Z",
        "total_cents": 4500,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV435.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C431"}, by="id")
