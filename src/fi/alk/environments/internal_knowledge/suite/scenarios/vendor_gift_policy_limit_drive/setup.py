def setup(world):
    """The conversation is with Ravi Shah (E18), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E18"}, by="id")
