def setup(world):
    """The base world; the session names the person talking."""
    world.put("callers", {"caller_id": "C401", "full_name": "Elena Navarro", "organisation": None})
    world.change("session_caller", "default", {"caller_id": "C401"}, by="id")
