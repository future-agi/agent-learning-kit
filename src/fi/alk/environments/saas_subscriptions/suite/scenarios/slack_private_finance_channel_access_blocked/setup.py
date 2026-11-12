def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_AM11", "name": "Kevin Zhao", "email": "kevin.zhao@cloudnest.example", "role": "account_manager"})
    world.change("session_caller", "default", {"actor_id": "U_AM11"}, by="id")
