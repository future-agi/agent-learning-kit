def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_AM9", "name": "Noah Tremblay", "email": "noah.tremblay@cloudnest.example", "role": "account_manager"})
    world.put("account_assignments", {"tenant_id": "T11", "user_id": "U_AM9"})
    world.change("session_caller", "default", {"actor_id": "U_AM9"}, by="id")
