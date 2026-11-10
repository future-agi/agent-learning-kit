def setup(world):
    world.put("customers", {
        "customer_id": "C476",
        "name": "Fiona Gallagher",
        "email": "fiona.gallagher@example.com.au",
        "phone": "+615550476",
        "postal_code": "3000",
        "address": "100 Flinders St, Melbourne",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O477",
        "customer_id": "C476",
        "order_date": "2026-10-18T14:00:00Z",
        "delivery_date": "2026-10-22T11:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 7500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 7500,
        "shipping_address": "100 Flinders St, Melbourne 3000",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT479",
        "order_id": "O477",
        "sku": "AP08",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 7500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 7500,
    })
    world.put("invoices", {
        "invoice_id": "INV477",
        "order_id": "O477",
        "issued_at": "2026-10-18T14:00:00Z",
        "total_cents": 7500,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV477.pdf",
    })
    world.put("shipments", {
        "shipment_id": "SHP477",
        "order_id": "O477",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000477",
        "status": "delivered",
        "shipped_at": "2026-10-18T18:00:00Z",
        "delivered_at": "2026-10-22T11:00:00Z",
        "last_event": "Delivered, left at front door",
        "last_event_at": "2026-10-22T11:00:00Z",
    })
