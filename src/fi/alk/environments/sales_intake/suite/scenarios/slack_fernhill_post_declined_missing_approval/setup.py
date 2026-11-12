def setup(world):
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S420", "name": "Tom Ashby", "email": "tom.ashby@brightdesk.example", "role": "sales_ops"})
    world.change("session_caller", "default", {"person_id": "U_S420"}, by="id")
