def setup(world):
    world.put("customers", {
        "customer_id": "CU401",
        "name": "Layla Mahmoud",
        "email": "layla.mahmoud@example.com",
        "phone": "+44 20 7946 0401",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-10-20T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU401", "desk_id": None}, by="id")
