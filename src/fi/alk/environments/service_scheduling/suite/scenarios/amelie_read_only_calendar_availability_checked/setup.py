def setup(world):
    world.put("customers", {
        "customer_id": "CU406",
        "name": "Sophie Martin",
        "email": "sophie.martin@example.com",
        "phone": "+1 416 555 0406",
        "preferred_language": "en",
        "time_zone": "America/Toronto",
        "created_at": "2026-10-26T09:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU406", "desk_id": None}, by="id")
