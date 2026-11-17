def setup(world):
    world.put("customers", {
        "customer_id": "C414",
        "name": "Wanda Maximoff",
        "email": "wanda.maximoff@example.com",
        "phone": "+15550414",
        "postal_code": "07030",
        "address": "700 Washington St, Hoboken, NJ 07030",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O414",
        "customer_id": "C414",
        "order_date": "2026-10-18T10:00:00Z",
        "delivery_date": "2026-10-22T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 5500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 5500,
        "shipping_address": "700 Washington St, Hoboken, NJ 07030",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT414",
        "order_id": "O414",
        "sku": "SH09",
        "quantity": 1,
        "unit_price_cents": 5500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 5500,
        "returned_quantity": 0,
    })
    world.put("shipments", {
        "shipment_id": "SHP414",
        "order_id": "O414",
        "tracking_number": "1Z999AA1000000414",
        "carrier": "UPS",
        "status": "delivered",
        "shipped_at": "2026-10-19T10:00:00Z",
        "delivered_at": "2026-10-22T10:00:00Z",
        "last_event": "Delivered",
        "last_event_at": "2026-10-22T10:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV414",
        "order_id": "O414",
        "issued_at": "2026-10-18T10:00:00Z",
        "total_cents": 5500,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV414.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C414"}, by="id")
