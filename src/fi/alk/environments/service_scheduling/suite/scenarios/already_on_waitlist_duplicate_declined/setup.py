def setup(world):
    world.put("customers", {
        "customer_id": "CU413",
        "name": "Nina Johansson",
        "email": "nina.johansson@example.com",
        "phone": "+44 20 7946 0413",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-10-26T09:00:00Z"
    })
    world.put("waitlist", {
        "waitlist_id": "W401",
        "customer_id": "CU413",
        "service_id": "SV2",
        "location_id": "L1",
        "staff_id": None,
        "preferred_date": "2026-10-29",
        "status": "active",
        "created_at": "2026-10-22T12:00:00Z"
    })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU413", "desk_id": None}, by="id")
