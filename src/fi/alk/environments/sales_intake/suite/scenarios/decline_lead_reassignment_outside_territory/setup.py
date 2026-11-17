def setup(world):
    """This scenario runs on the base world unchanged."""
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S403", "name": "Toby Ross", "email": "toby.ross@brightdesk.example", "role": "sales_ops"})
    world.change("session_caller", "default", {"person_id": "U_S403"}, by="id")
