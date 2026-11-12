def setup(world):
    world.put("customers", {
        "customer_id": "CU412",
        "name": "Marcus Evans",
        "email": "marcus.evans@example.com",
        "phone": "+44 20 7946 0412",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-10-26T09:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU412", "desk_id": None}, by="id")
