def setup(world):
    """The base world; the session names the person talking."""
    world.change("session_caller", "default", {"caller_id": "P14"}, by="id")
