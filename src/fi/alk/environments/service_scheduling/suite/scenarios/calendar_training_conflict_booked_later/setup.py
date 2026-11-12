def setup(world):
    world.put("customers", {
        "customer_id": "CU406",
        "name": "Aarav Sharma",
        "email": "aarav.sharma@example.com",
        "phone": "+91 98200 00406",
        "preferred_language": "en",
        "time_zone": "Asia/Kolkata",
        "created_at": "2026-10-20T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU406", "desk_id": None}, by="id")
