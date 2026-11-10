def setup(world):
    world.put("returns", {
        "return_id": "R422",
        "order_id": "O22",
        "customer_id": "C02",
        "status": "authorized",
        "return_reason": "AP04:too_large",
        "estimated_refund_cents": 6200,
        "restocking_fee_cents": 0,
        "request_key": "REQ-C02-O22",
        "created_at": "2026-10-25T09:00:00Z",
        "cancelled_at": None
    })
    world.put("return_items", {
        "return_item_id": "RI422",
        "return_id": "R422",
        "item_id": "IT22",
        "sku": "AP04",
        "quantity": 1,
        "refunded_amount_cents": 6200
    })
    world.put("return_labels", {
        "label_id": "L422",
        "return_id": "R422",
        "tracking_number": "9400111899223190000422",
        "carrier": "USPS",
        "label_url": "https://labels.example.com/L422.pdf",
        "status": "active",
        "created_at": "2026-10-25T09:00:00Z"
    })
