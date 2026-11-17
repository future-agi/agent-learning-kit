def setup(world):
    world.put("customers", {
        "customer_id": "C451",
        "name": "Wanda Maximoff",
        "email": "wanda.maximoff@example.com",
        "phone": "+15550451",
        "postal_code": "98101",
        "address": "12 Old Oak Lane",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O451",
        "customer_id": "C451",
        "order_date": "2026-10-25T10:00:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 3400,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 3400,
        "shipping_address": "12 Old Oak Lane, 98101",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT451",
        "order_id": "O451",
        "sku": "AP02",
        "quantity": 1,
        "unit_price_cents": 3400,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 3400,
        "returned_quantity": 0,
    })
    world.put("shipments", {
        "shipment_id": "SHP451",
        "order_id": "O451",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000451",
        "status": "label_created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Order being prepared at the warehouse",
        "last_event_at": "2026-10-25T10:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV451",
        "order_id": "O451",
        "issued_at": "2026-10-25T10:00:00Z",
        "total_cents": 3400,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV451.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C451"}, by="id")
