def setup(world):
    world.put("customers", {
        "customer_id": "CU402",
        "name": "Priya Nair",
        "email": "priya.nair@example.com",
        "phone": "+91 98200 00402",
        "preferred_language": "en",
        "time_zone": "Asia/Kolkata",
        "created_at": "2026-10-26T09:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU402", "desk_id": None}, by="id")
