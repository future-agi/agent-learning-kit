def setup(world):
    world.put("returns", {
        "return_id": "R01",
        "order_id": "O05",
        "customer_id": "C05",
        "created_at": "2026-10-26T10:00:00Z",
        "status": "authorized",
        "estimated_refund_cents": 7800,
        "restocking_fee_cents": 0,
        "return_reason": "AP05:defective"
    })
    world.put("return_items", {
        "return_item_id": "RI01",
        "return_id": "R01",
        "item_id": "IT05",
        "sku": "AP05",
        "quantity": 1,
        "refunded_amount_cents": 7800
    })
