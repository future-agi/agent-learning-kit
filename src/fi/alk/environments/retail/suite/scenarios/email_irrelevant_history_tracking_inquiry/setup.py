def setup(world):
    world.put("customers", {
        "customer_id": "C441",
        "name": "Natasha Romanoff",
        "email": "natasha.romanoff@example.com",
        "phone": "+15550441",
        "postal_code": "60602",
        "address": "441 Spyglass Way",
        "store_credit_cents": 0,
    })
    world.put("orders", {
        "order_id": "O441",
        "customer_id": "C441",
        "order_date": "2026-10-24T10:00:00Z",
        "delivery_date": None,
        "status": "in_transit",
        "currency": "USD",
        "total_gross_cents": 9500,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 9500,
        "shipping_address": "441 Spyglass Way, 60602",
        "cancelled_at": None,
    })
    world.put("order_items", {
        "item_id": "IT441",
        "order_id": "O441",
        "sku": "SH02",
        "quantity": 1,
        "unit_price_cents": 9500,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 9500,
        "returned_quantity": 0,
    })
    world.put("shipments", {
        "shipment_id": "SHP441",
        "order_id": "O441",
        "carrier": "UPS",
        "tracking_number": "1Z999AA10000000441",
        "status": "in_transit",
        "shipped_at": "2026-10-25T08:00:00Z",
        "delivered_at": None,
        "last_event": "In transit, arrived at sorting facility",
        "last_event_at": "2026-10-26T06:00:00Z",
    })
    world.put("invoices", {
        "invoice_id": "INV441",
        "order_id": "O441",
        "issued_at": "2026-10-24T10:00:00Z",
        "total_cents": 9500,
        "currency": "USD",
        "pdf_url": "https://invoices.example.com/INV441.pdf",
    })
    # The email she mentions from weeks ago, on another topic: it exists, and it is not what she asks.
    store = "support@northgate-outfitters.example"
    world.put("gmail_threads", {
        "id": "th_r441_gift_wrap",
        "subject": "Gift wrapping",
        "snippet": "Gift wrapping is not offered at the moment.",
        "history_id": "rhist_441",
        "is_restricted": 0,
        "updated_at": "2026-10-03T12:00:00Z",
    })
    for mid, sender, recipient, date, body in (
        ("rmsg_441", "natasha.romanoff@example.com", store, "2026-10-02T18:00:00Z",
         "Hello, do you offer gift wrapping on orders? I am buying a present for my brother."),
        ("rmsg_442", store, "natasha.romanoff@example.com", "2026-10-03T12:00:00Z",
         "Dear Natasha Romanoff, gift wrapping is not offered at the moment, but every order can include a gift receipt without prices."),
    ):
        world.put("gmail_messages", {
            "id": mid,
            "thread_id": "th_r441_gift_wrap",
            "sender": sender,
            "recipient": recipient,
            "subject": ("" if mid == "rmsg_441" else "Re: ") + "Gift wrapping",
            "date": date,
            "snippet": body[:80],
            "body": body,
            "cc": "",
        })
    world.put("gmail_thread_labels", {"thread_id": "th_r441_gift_wrap", "label_id": "INBOX"})
    world.put("gmail_thread_labels", {"thread_id": "th_r441_gift_wrap", "label_id": "handled"})
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C441"}, by="id")
