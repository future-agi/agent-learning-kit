def setup(world):
    world.put("customers", {
        "customer_id": "CU411",
        "name": "Cameron Murray",
        "email": "cameron.murray@example.com",
        "phone": "+44 20 7946 0411",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-01-15T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU411", "desk_id": None}, by="id")
