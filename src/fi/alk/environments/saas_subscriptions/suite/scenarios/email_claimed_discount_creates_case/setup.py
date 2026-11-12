def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_AM6", "name": "Rohan Varma", "email": "rohan.varma@cloudnest.example", "role": "account_manager"})
    world.put("account_assignments", {"tenant_id": "T02", "user_id": "U_AM6"})
    world.change("session_caller", "default", {"actor_id": "U_AM6"}, by="id")
