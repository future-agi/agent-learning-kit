def setup(world):
    """This scenario runs on the base world unchanged."""
    world.put("slack_users", {"user_id": "U_BL3", "name": "Nadia Brooks", "email": "nadia.brooks@cloudnest.example", "role": "billing_lead"})
    world.change("session_caller", "default", {"actor_id": "U_BL3"}, by="id")
