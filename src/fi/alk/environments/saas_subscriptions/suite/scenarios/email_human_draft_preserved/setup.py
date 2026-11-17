def setup(world):
    """Billing support working the inbox; the thread and Sam's draft are in the base world."""
    world.put("slack_users", {"user_id": "U_BS2", "name": "Maya Lin", "email": "maya.lin@cloudnest.example", "role": "billing_support"})
    world.change("session_caller", "default", {"actor_id": "U_BS2"}, by="id")
