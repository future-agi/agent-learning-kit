def setup(world):
    """The conversation is with Kenji Tanaka (E10), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E10"}, by="id")
