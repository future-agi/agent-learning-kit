def setup(world):
    world.put("customers", {
        "customer_id": "C472",
        "name": "Lucas Chen",
        "email": "lucas.chen@example.com",
        "phone": "+15550472",
        "postal_code": "94107",
        "address": "220 King St",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O473",
        "customer_id": "C472",
        "order_date": "2026-10-26T08:00:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 8000,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 8000,
        "shipping_address": "220 King St, 94107",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT474",
        "order_id": "O473",
        "sku": "SH01",
        "quantity": 1,
        "returned_quantity": 0,
        "unit_price_cents": 8000,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 8000,
    })
    world.put("shipments", {
        "shipment_id": "SHP473",
        "order_id": "O473",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000473",
        "status": "created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T08:05:00Z",
    })
    world.put("gmail_threads", {
        "id": "th_r470_cancel_then_keep",
        "subject": "Question regarding my order O473",
        "snippet": "Never mind, keep O473 and ship it.",
        "history_id": "rhist_470",
        "is_restricted": 0,
        "updated_at": "2026-10-26T10:00:00Z",
    })
    world.put("gmail_messages", {
        "id": "rmsg_470",
        "thread_id": "th_r470_cancel_then_keep",
        "sender": "lucas.chen@example.com",
        "recipient": "support@northgate-outfitters.example",
        "subject": "Question regarding my order O473",
        "date": "2026-10-26T09:00:00Z",
        "snippet": "Please cancel order O473",
        "body": "Hello, please cancel order O473. Thank you.",
        "cc": "",
    })
    world.put("gmail_messages", {
        "id": "rmsg_471",
        "thread_id": "th_r470_cancel_then_keep",
        "sender": "lucas.chen@example.com",
        "recipient": "support@northgate-outfitters.example",
        "subject": "Re: Question regarding my order O473",
        "date": "2026-10-26T10:00:00Z",
        "snippet": "Never mind, keep O473 and ship it.",
        "body": "Never mind, please keep order O473 and ship it as planned. Do not cancel it.",
        "cc": "",
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_r470_cancel_then_keep",
        "label_id": "INBOX",
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_r470_cancel_then_keep",
        "label_id": "UNREAD",
    })
