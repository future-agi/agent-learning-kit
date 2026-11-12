def setup(world):
    world.put("customers", {
        "customer_id": "CU413",
        "name": "Mei-Ling Chen",
        "email": "meiling.chen@example.com",
        "phone": "+61 2 9555 0413",
        "preferred_language": "en",
        "time_zone": "Australia/Sydney",
        "created_at": "2026-10-20T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU413", "desk_id": None}, by="id")
