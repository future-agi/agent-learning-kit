def setup(world):
    world.put("customers", {
        "customer_id": "CU402",
        "name": "Liam Gallagher",
        "email": "liam.gallagher@example.com",
        "phone": "+1 212 555 0402",
        "preferred_language": "en",
        "time_zone": "America/New_York",
        "created_at": "2026-01-15T12:00:00Z"
    })
    world.put("customer_preferences", {
        "pref_id": "PF402",
        "customer_id": "CU402",
        "kind": "staff",
        "value": "ST01",
        "updated_at": "2026-06-01T12:00:00Z"
    })
