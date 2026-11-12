def setup(world):
    world.put("customers", {
        "customer_id": "CU415",
        "name": "Hannah Abbott",
        "email": "hannah.abbott@example.co.uk",
        "phone": "+44 20 7946 0415",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-10-20T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU415", "desk_id": None}, by="id")
