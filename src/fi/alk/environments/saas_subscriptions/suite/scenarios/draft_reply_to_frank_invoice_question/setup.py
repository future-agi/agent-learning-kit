def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("account_assignments", {"tenant_id": "T06", "user_id": "U_AM2"})
    world.change("session_caller", "default", {"actor_id": "U_AM2"}, by="id")
