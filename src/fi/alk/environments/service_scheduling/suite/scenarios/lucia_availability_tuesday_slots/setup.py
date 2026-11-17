def setup(world):
    world.put("customers", {
        "customer_id": "CU101",
        "name": "Maya Campbell",
        "email": "maya.campbell@example.com",
        "phone": "+1 416 555 0201",
        "preferred_language": "en",
        "time_zone": "America/Toronto",
        "created_at": "2026-01-15T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU101", "desk_id": None}, by="id")
