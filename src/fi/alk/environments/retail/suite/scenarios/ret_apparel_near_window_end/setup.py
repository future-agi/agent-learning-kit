def setup(world):
    world.put("customers", {
        "customer_id": "C405",
        "name": "Liam O'Connor",
        "email": "liam.oconnor@example.com",
        "phone": "+15550405",
        "postal_code": "97201",
        "address": "500 SW 5th Ave, Portland, OR 97201",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O405",
        "customer_id": "C405",
        "order_date": "2026-09-24T10:00:00Z",
        "delivery_date": "2026-09-28T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 6800,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 6800,
        "shipping_address": "500 SW 5th Ave, Portland, OR 97201",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT405",
        "order_id": "O405",
        "sku": "AP06",
        "quantity": 1,
        "unit_price_cents": 6800,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 6800,
        "returned_quantity": 0,
    })
    world.put("shipments", {
        "shipment_id": "SHP405",
        "order_id": "O405",
        "tracking_number": "1Z999AA1000000405",
        "carrier": "UPS",
        "status": "delivered",
        "shipped_at": "2026-09-25T10:00:00Z",
        "delivered_at": "2026-09-28T10:00:00Z",
        "last_event": "Delivered",
        "last_event_at": "2026-09-28T10:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV405",
        "order_id": "O405",
        "issued_at": "2026-09-24T10:00:00Z",
        "total_cents": 6800,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV405.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C405"}, by="id")
