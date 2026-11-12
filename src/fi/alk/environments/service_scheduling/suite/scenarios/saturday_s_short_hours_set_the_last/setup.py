def setup(world):
    world.put("customers", {
        "customer_id": "CU441",
        "name": "Sienna Clarke",
        "email": "sienna.clarke@example.com",
        "phone": "+44 20 7946 0441",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-01-15T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU441", "desk_id": None}, by="id")
