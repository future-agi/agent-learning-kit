def setup(world):
    world.put("customers", {
        "customer_id": "C402",
        "name": "Elena Rostova",
        "email": "elena.rostova@example.com",
        "phone": "+15550402",
        "postal_code": "98101",
        "address": "456 Pike St, Seattle, WA 98101",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O402",
        "customer_id": "C402",
        "order_date": "2026-10-18T10:00:00Z",
        "delivery_date": "2026-10-22T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 7300,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 7300,
        "shipping_address": "456 Pike St, Seattle, WA 98101",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT402_1",
        "order_id": "O402",
        "sku": "SH05",
        "quantity": 1,
        "unit_price_cents": 4500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 4500,
        "returned_quantity": 0,
    })
    world.put("order_items", {
        "item_id": "IT402_2",
        "order_id": "O402",
        "sku": "AP01",
        "quantity": 1,
        "unit_price_cents": 2800,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 2800,
        "returned_quantity": 0,
    })
    world.put("shipments", {
        "shipment_id": "SHP402",
        "order_id": "O402",
        "tracking_number": "1Z999AA1000000402",
        "carrier": "UPS",
        "status": "delivered",
        "shipped_at": "2026-10-19T10:00:00Z",
        "delivered_at": "2026-10-22T10:00:00Z",
        "last_event": "Delivered",
        "last_event_at": "2026-10-22T10:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV402",
        "order_id": "O402",
        "issued_at": "2026-10-18T10:00:00Z",
        "total_cents": 7300,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV402.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C402"}, by="id")
