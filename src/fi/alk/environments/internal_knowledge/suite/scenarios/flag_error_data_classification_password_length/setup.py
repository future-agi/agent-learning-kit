def setup(world):
    """The conversation is with Aisha Rahman (E07), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E07"}, by="id")
