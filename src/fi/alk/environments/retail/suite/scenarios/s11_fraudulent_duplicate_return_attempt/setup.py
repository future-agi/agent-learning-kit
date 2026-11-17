def setup(world):
    world.put("returns", {
        "return_id": "RET-O11-01",
        "order_id": "O11",
        "customer_id": "C11",
        "created_at": "2026-10-20T10:00:00Z",
        "status": "authorized",
        "estimated_refund_cents": 8500,
        "restocking_fee_cents": 0,
        "return_reason": "unwanted"
    })
    world.put("return_items", {
        "return_item_id": "RI-O11-01",
        "return_id": "RET-O11-01",
        "item_id": "IT11",
        "sku": "AC07",
        "quantity": 1,
        "refunded_amount_cents": 8500
    })
    world.put("return_labels", {
        "label_id": "LBL-O11-01",
        "return_id": "RET-O11-01",
        "tracking_number": "940011189922319000011",
        "carrier": "USPS",
        "label_url": "https://labels.example.com/LBL-O11-01.pdf",
        "status": "active",
        "created_at": "2026-10-20T10:00:00Z"
    })
