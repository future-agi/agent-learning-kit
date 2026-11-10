def setup(world):
    world.put("customers", {
        "customer_id": "C407",
        "name": "Grace Harrison",
        "email": "grace.harrison@example.com",
        "phone": "+15550407",
        "postal_code": "02138",
        "address": "19 Brattle St",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O407",
        "customer_id": "C407",
        "order_date": "2026-10-24T18:00:00Z",
        "delivery_date": None,
        "status": "in_transit",
        "currency": "USD",
        "total_gross_cents": 11000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 11000,
        "shipping_address": "19 Brattle St, 02138",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT407",
        "order_id": "O407",
        "sku": "SH02",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 11000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 11000,
    })
    world.put("shipments", {
        "shipment_id": "SHP407",
        "order_id": "O407",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000407",
        "status": "in_transit",
        "shipped_at": "2026-10-24T18:00:00Z",
        "delivered_at": None,
        "last_event": "Departed regional facility, Louisville KY",
        "last_event_at": "2026-10-25T22:10:00Z",
    })
