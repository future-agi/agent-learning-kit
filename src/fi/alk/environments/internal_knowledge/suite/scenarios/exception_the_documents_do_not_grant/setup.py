def setup(world):
    """The conversation is with Marcus Lee (E02), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E02"}, by="id")
