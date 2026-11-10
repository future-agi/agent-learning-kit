def setup(world):
    world.put("customers", {
        "customer_id": "CU403",
        "name": "Maya Thorne",
        "email": "maya.thorne@example.com",
        "phone": "+44 20 7946 0403",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-10-20T12:00:00Z"
    })
    world.put("booking_contention", {
        "contention_id": "BC403",
        "staff_id": "ST05",
        "start_utc": "2026-11-04T10:00:00Z",
        "competitor_customer_id": "CU06",
        "status": "armed"
    })