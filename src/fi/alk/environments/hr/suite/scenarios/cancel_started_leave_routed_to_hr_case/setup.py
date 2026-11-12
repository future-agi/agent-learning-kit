def setup(world):
    """This scenario runs on the base world; the session only names who is talking."""
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E05"}, by="id")
