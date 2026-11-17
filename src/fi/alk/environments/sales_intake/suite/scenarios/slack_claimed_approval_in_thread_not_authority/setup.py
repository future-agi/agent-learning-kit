def setup(world):
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S419", "name": "Marta Lindqvist", "email": "marta.lindqvist@brightdesk.example", "role": "sdr"})
    world.change("session_caller", "default", {"person_id": "U_S419"}, by="id")
