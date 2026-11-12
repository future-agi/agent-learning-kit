def setup(world):
    world.put("customers", {
        "customer_id": "C423",
        "name": "Elena Rostova",
        "email": "elena.rostova@example.com",
        "phone": "+61491570423",
        "postal_code": "30301",
        "address": "45 George St, Sydney",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O423",
        "customer_id": "C423",
        "order_date": "2026-10-24T10:00:00Z",
        "delivery_date": None,
        "status": "in_transit",
        "shipping_address": "45 George St, Sydney, 30301",
        "currency": "USD",
        "total_gross_cents": 2800,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 2800,
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT423",
        "order_id": "O423",
        "sku": "AP01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 2800,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 2800,
    })
    world.put("invoices", {
        "invoice_id": "INV423",
        "order_id": "O423",
        "issued_at": "2026-10-24T10:00:00Z",
        "total_cents": 2800,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV423.pdf",
    })
    world.put("shipments", {
        "shipment_id": "SHP423",
        "order_id": "O423",
        "tracking_number": "1Z999AA10000000423",
        "carrier": "UPS",
        "status": "in_transit",
        "shipped_at": "2026-10-24T18:00:00Z",
        "delivered_at": None,
        "last_event": "In transit",
        "last_event_at": "2026-10-25T08:00:00Z",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C423"}, by="id")
