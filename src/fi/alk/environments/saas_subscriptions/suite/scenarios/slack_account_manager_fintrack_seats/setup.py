def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_AM8", "name": "Rohan Mehta", "email": "rohan.mehta@cloudnest.example", "role": "account_manager"})
    world.put("account_assignments", {"tenant_id": "T06", "user_id": "U_AM8"})
    world.change("session_caller", "default", {"actor_id": "U_AM8"}, by="id")
