def setup(world):
    """The conversation is with Lucas Martin (E14), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E14"}, by="id")
