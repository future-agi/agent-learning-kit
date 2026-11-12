def setup(world):
    """The conversation is with Grace Kim (E05), signed in to the session."""
    world.change("session_caller", "default", {"employee_id": "E05"}, by="id")
