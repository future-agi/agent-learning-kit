def setup(world):
    """The conversation is with Oliver Grant (E08), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E08"}, by="id")
