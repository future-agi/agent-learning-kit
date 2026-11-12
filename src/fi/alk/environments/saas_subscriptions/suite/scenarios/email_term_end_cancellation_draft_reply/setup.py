def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_OPS1", "name": "Kendra Price", "email": "kendra.price@cloudnest.example", "role": "billing_operations"})
    world.change("session_caller", "default", {"actor_id": "U_OPS1"}, by="id")
