def setup(world):
    world.put("returns", {
        "return_id": "RET-O02-01",
        "order_id": "O02",
        "customer_id": "C02",
        "created_at": "2026-10-20T10:00:00Z",
        "status": "authorized",
        "estimated_refund_cents": 9500,
        "restocking_fee_cents": 0,
        "return_reason": "SH02:wrong_size",
    })
    world.put("return_items", {"return_item_id": "RI-O02-01", "return_id": "RET-O02-01", "item_id": "IT02", "sku": "SH02", "quantity": 1, "refunded_amount_cents": 9500})
    world.put("return_labels", {
        "label_id": "LBL-O02-01",
        "return_id": "RET-O02-01",
        "tracking_number": "940011189922319000002",
        "carrier": "USPS",
        "label_url": "https://labels.example.com/LBL-O02-01.pdf",
        "status": "active",
        "created_at": "2026-10-20T10:00:00Z",
    })
    world.change("order_items", "IT02", {"returned_quantity": 1}, by="item_id")
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C02"}, by="id")
