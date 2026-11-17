def setup(world):
    """The conversation is with Emma Wilson (E19), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E19"}, by="id")
