def setup(world):
    """The conversation is with Sam Taylor (E20), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E20"}, by="id")
