def setup(world):
    world.put("customers", {
        "customer_id": "C434",
        "name": "Carlos Mendoza",
        "email": "carlos.mendoza@example.com",
        "phone": "+15550434",
        "postal_code": "33101",
        "address": "500 Brickell Ave",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O434",
        "customer_id": "C434",
        "order_date": "2026-10-20T10:00:00Z",
        "delivery_date": "2026-10-25T11:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 9500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 9500,
        "shipping_address": "500 Brickell Ave, 33101",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT434",
        "order_id": "O434",
        "sku": "SH02",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 9500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 9500,
    })
    world.put("shipments", {
        "shipment_id": "SHP434",
        "order_id": "O434",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000434",
        "status": "delivered",
        "shipped_at": "2026-10-21T18:00:00Z",
        "delivered_at": "2026-10-25T11:00:00Z",
        "last_event": "Delivered, left at front door",
        "last_event_at": "2026-10-25T11:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV434",
        "order_id": "O434",
        "issued_at": "2026-10-20T10:00:00Z",
        "total_cents": 9500,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV434.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C434"}, by="id")
