def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_AM4", "name": "Ananya Rao", "email": "ananya.rao@cloudnest.example", "role": "account_manager"})
    world.put("account_assignments", {"tenant_id": "T09", "user_id": "U_AM4"})
    world.change("session_caller", "default", {"actor_id": "U_AM4"}, by="id")
