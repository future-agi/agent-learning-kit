def setup(world):
    """The conversation is with Nora Walsh (E15), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E15"}, by="id")
