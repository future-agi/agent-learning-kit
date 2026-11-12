def setup(world):
    world.put("customers", {
        "customer_id": "CU403",
        "name": "Clara Vance",
        "email": "clara.vance@example.com",
        "phone": "+1 212 555 0403",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU403", "desk_id": None}, by="id")
