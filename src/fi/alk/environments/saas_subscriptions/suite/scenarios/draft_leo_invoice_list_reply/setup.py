def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_AM5", "name": "Liam Davies", "email": "liam.davies@cloudnest.example", "role": "account_manager"})
    world.put("account_assignments", {"tenant_id": "T12", "user_id": "U_AM5"})
    world.change("session_caller", "default", {"actor_id": "U_AM5"}, by="id")
