def setup(world):
    world.put("customers", {
        "customer_id": "CU404",
        "name": "Kiran Patel",
        "email": "kiran.patel@example.com",
        "phone": "+1 416 555 0404",
        "preferred_language": "en",
        "time_zone": "America/Toronto",
        "created_at": "2026-10-20T12:00:00Z"
    })
    world.put("booking_contention", {
        "contention_id": "BC404",
        "staff_id": "ST02",
        "start_utc": "2026-11-03T19:00:00Z",
        "competitor_customer_id": "CU04",
        "status": "armed"
    })