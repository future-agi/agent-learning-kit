def setup(world):
    world.put("booking_contention", {
        "contention_id": "BC405",
        "staff_id": "ST04",
        "start_utc": "2026-11-04T11:00:00Z",
        "competitor_customer_id": "CU07",
        "status": "armed"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU08", "desk_id": None}, by="id")
