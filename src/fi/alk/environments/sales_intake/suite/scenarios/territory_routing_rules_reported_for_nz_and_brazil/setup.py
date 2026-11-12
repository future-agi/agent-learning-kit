def setup(world):
    """This scenario runs on the base world unchanged."""
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S425", "name": "Callum MacLeod", "email": "callum.macleod@brightdesk.example", "role": "sdr"})
    world.change("session_caller", "default", {"person_id": "U_S425"}, by="id")
