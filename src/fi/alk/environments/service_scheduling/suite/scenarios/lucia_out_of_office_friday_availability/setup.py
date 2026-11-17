def setup(world):
    world.put("customers", {
        "customer_id": "CU407",
        "name": "Lucas Silva",
        "email": "lucas.silva@example.com",
        "phone": "+61 2 9555 0407",
        "preferred_language": "en",
        "time_zone": "Australia/Sydney",
        "created_at": "2026-10-26T09:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU407", "desk_id": None}, by="id")
