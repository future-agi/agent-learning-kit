def setup(world):
    """The conversation is with Sofia Rossi (E09), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E09"}, by="id")
