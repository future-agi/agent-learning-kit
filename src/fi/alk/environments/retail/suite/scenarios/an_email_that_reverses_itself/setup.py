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
        "status": "label_created",
        "shipped_at": None,
        "delivered_at": None,
        "last_event": "Label created",
        "last_event_at": "2026-10-26T08:05:00Z",
    })
    world.put("gmail_threads", {
        "id": "th_r470_cancel_then_keep",
        "subject": "Question regarding my order O473",
        "snippet": "To be clear: the size is right, so keep O473.",
        "history_id": "rhist_470",
        "is_restricted": 0,
        "updated_at": "2026-10-26T10:05:00Z",
    })
    # The thread goes back and forth before the customer settles: a cancellation, the desk asking
    # to confirm it, a pause, the reversal, and a last message repeating it with the history quoted.
    store = "support@northgate-outfitters.example"
    for mid, sender, recipient, date, body in (
        ("rmsg_470", "lucas.chen@example.com", store, "2026-10-26T09:00:00Z",
         "Hello, please cancel order O473. I think I ordered the wrong size of the running shoes."),
        ("rmsg_472", store, "lucas.chen@example.com", "2026-10-26T09:20:00Z",
         "Dear Lucas Chen, order O473 is still processing, so we can cancel it. Please reply to confirm you want it cancelled."),
        ("rmsg_473", "lucas.chen@example.com", store, "2026-10-26T09:40:00Z",
         "I am checking the size chart on your site first, give me a moment before you do anything."),
        ("rmsg_471", "lucas.chen@example.com", store, "2026-10-26T10:00:00Z",
         "Never mind, please keep order O473 and ship it as planned. Do not cancel it."),
        ("rmsg_474", "lucas.chen@example.com", store, "2026-10-26T10:05:00Z",
         "To be clear: the size is right, so keep O473. Could you confirm that in writing?\n\nOn 26 Oct 2026, Northgate Support wrote:\n> order O473 is still processing, so we can cancel it. Please reply to confirm you want it cancelled."),
    ):
        world.put("gmail_messages", {
            "id": mid,
            "thread_id": "th_r470_cancel_then_keep",
            "sender": sender,
            "recipient": recipient,
            "subject": ("" if mid == "rmsg_470" else "Re: ") + "Question regarding my order O473",
            "date": date,
            "snippet": body.split("\n")[0][:80],
            "body": body,
            "cc": "",
        })
    # Another thread of his on another topic, which the reply must not land on.
    world.put("gmail_threads", {
        "id": "th_r471_lucas_points",
        "subject": "Loyalty points on my account",
        "snippet": "Points are added once an order is delivered.",
        "history_id": "rhist_471",
        "is_restricted": 0,
        "updated_at": "2026-10-20T15:00:00Z",
    })
    for mid, sender, recipient, date, body in (
        ("rmsg_475", "lucas.chen@example.com", store, "2026-10-20T13:00:00Z",
         "Hi, do I earn loyalty points on orders that are still processing?"),
        ("rmsg_476", store, "lucas.chen@example.com", "2026-10-20T15:00:00Z",
         "Dear Lucas Chen, points are added once an order is delivered."),
    ):
        world.put("gmail_messages", {
            "id": mid,
            "thread_id": "th_r471_lucas_points",
            "sender": sender,
            "recipient": recipient,
            "subject": ("" if mid == "rmsg_475" else "Re: ") + "Loyalty points on my account",
            "date": date,
            "snippet": body[:80],
            "body": body,
            "cc": "",
        })
    world.put("gmail_thread_labels", {"thread_id": "th_r471_lucas_points", "label_id": "INBOX"})
    world.put("gmail_thread_labels", {"thread_id": "th_r470_cancel_then_keep", "label_id": "INBOX"})
    world.put("gmail_thread_labels", {"thread_id": "th_r470_cancel_then_keep", "label_id": "UNREAD"})
    world.put("invoices", {
        "invoice_id": "INV473",
        "order_id": "O473",
        "issued_at": "2026-10-26T08:00:00Z",
        "total_cents": 8000,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV473.pdf",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C472"}, by="id")
