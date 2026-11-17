def setup(world):
    pass
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "M04"}, by="id")
