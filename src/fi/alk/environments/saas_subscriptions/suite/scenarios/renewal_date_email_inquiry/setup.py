def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_BS1", "name": "Sam Billing", "email": "sam.billing@cloudnest.example", "role": "billing_support"})
    world.change("session_caller", "default", {"actor_id": "U_BS1"}, by="id")
