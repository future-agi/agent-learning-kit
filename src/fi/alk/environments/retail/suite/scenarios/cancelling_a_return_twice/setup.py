def setup(world):
    world.put("customers", {
        "customer_id": "C475",
        "name": "Ananya Iyer",
        "email": "ananya.iyer@example.in",
        "phone": "+915550475",
        "postal_code": "560001",
        "address": "42 MG Road, Bangalore",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O476",
        "customer_id": "C475",
        "order_date": "2026-10-15T10:00:00Z",
        "delivery_date": "2026-10-19T10:00:00Z",
        "status": "delivered",
        "currency": "USD",
        "total_gross_cents": 6500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 6500,
        "shipping_address": "42 MG Road, Bangalore 560001",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT478",
        "order_id": "O476",
        "sku": "AP06",
        "quantity": 1,
        "returned_quantity": 1,
        "unit_price_cents": 6500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 6500,
    })
    world.put("returns", {
        "return_id": "R470",
        "order_id": "O476",
        "customer_id": "C475",
        "status": "authorized",
        "estimated_refund_cents": 6500,
        "restocking_fee_cents": 0,
        "return_reason": "AP06:size_fit",
        "request_key": "REQ-C475-O476",
        "created_at": "2026-10-22T10:00:00Z",
        "cancelled_at": None,
    })
    world.put("return_items", {
        "return_item_id": "RI470",
        "return_id": "R470",
        "item_id": "IT478",
        "sku": "AP06",
        "quantity": 1,
        "refunded_amount_cents": 6500,
    })
    world.put("return_labels", {
        "label_id": "L470",
        "return_id": "R470",
        "tracking_number": "9400111899223190000470",
        "carrier": "USPS",
        "label_url": "https://labels.example.com/L470.pdf",
        "status": "active",
        "created_at": "2026-10-22T10:00:00Z",
    })
    world.put("shipments", {"shipment_id": "SHP476", "order_id": "O476", "carrier": "UPS", "tracking_number": "1Z999AA10000000476", "status": "delivered", "shipped_at": "2026-10-15T18:00:00Z", "delivered_at": "2026-10-19T10:00:00Z", "last_event": "Delivered, left at front door", "last_event_at": "2026-10-19T10:00:00Z"})
    world.put("invoices", {"invoice_id": "INV476", "order_id": "O476", "issued_at": "2026-10-15T10:00:00Z", "total_cents": 6500, "currency": "USD", "pdf_url": "https://invoices.example.com/INV476.pdf"})
