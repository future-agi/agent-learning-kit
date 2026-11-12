def setup(world):
    world.put("returns", {
        "return_id": "R411",
        "order_id": "O11",
        "customer_id": "C11",
        "status": "authorized",
        "return_reason": "AC07:does_not_fit",
        "estimated_refund_cents": 8500,
        "restocking_fee_cents": 0,
        "request_key": "REQ-C11-O11",
        "created_at": "2026-10-24T10:00:00Z",
        "cancelled_at": None,
    })
    world.put("return_items", {"return_item_id": "RI411", "return_id": "R411", "item_id": "IT11", "sku": "AC07", "quantity": 1, "refunded_amount_cents": 8500})
    world.put("return_labels", {
        "label_id": "L411",
        "return_id": "R411",
        "tracking_number": "9400111899223190000411",
        "carrier": "USPS",
        "label_url": "https://labels.example.com/L411.pdf",
        "status": "active",
        "created_at": "2026-10-24T10:00:00Z",
    })
    world.change("order_items", "IT11", {"returned_quantity": 1}, by="item_id")
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C11"}, by="id")
