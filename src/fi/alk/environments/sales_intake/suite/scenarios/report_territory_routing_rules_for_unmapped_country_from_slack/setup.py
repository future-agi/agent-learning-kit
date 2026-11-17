def setup(world):
    """This scenario runs on the base world unchanged."""
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S415", "name": "Lucas Vance", "email": "lucas.vance@brightdesk.example", "role": "sdr"})
    world.change("session_caller", "default", {"person_id": "U_S415"}, by="id")
