def setup(world):
    """The base world; the session names the person talking."""
    world.put("callers", {"caller_id": "C401", "full_name": "Mateo Cruz", "organisation": None})
    world.put("caller_access", {"caller_id": "C401", "patient_id": "P19", "relationship": "husband, registered proxy"})
    world.change("session_caller", "default", {"caller_id": "C401"}, by="id")
