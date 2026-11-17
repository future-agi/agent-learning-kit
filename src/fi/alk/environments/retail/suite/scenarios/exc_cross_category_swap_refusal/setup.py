def setup(world):
    world.put("customers", {
        "customer_id": "C410",
        "name": "Nathan Drake",
        "email": "nathan.drake@example.com",
        "phone": "+15550410",
        "postal_code": "92101",
        "address": "555 Broadway, San Diego, CA 92101",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O410",
        "customer_id": "C410",
        "order_date": "2026-10-19T10:00:00Z",
        "delivery_date": "2026-10-23T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 1500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 1500,
        "shipping_address": "555 Broadway, San Diego, CA 92101",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT410",
        "order_id": "O410",
        "sku": "AC13",
        "quantity": 1,
        "unit_price_cents": 1500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 1500,
        "returned_quantity": 0,
    })
    world.put("shipments", {
        "shipment_id": "SHP410",
        "order_id": "O410",
        "tracking_number": "1Z999AA1000000410",
        "carrier": "UPS",
        "status": "delivered",
        "shipped_at": "2026-10-20T10:00:00Z",
        "delivered_at": "2026-10-23T10:00:00Z",
        "last_event": "Delivered",
        "last_event_at": "2026-10-23T10:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV410",
        "order_id": "O410",
        "issued_at": "2026-10-19T10:00:00Z",
        "total_cents": 1500,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV410.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C410"}, by="id")
