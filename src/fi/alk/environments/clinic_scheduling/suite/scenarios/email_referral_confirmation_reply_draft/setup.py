def setup(world):
    """The base world; the session names the person talking."""
    world.put("callers", {"caller_id": "C401", "full_name": "Edward Collins", "organisation": "Harbour Family Practice"})
    world.put("caller_access", {"caller_id": "C401", "patient_id": "P11", "relationship": "referring practice"})
    world.change("session_caller", "default", {"caller_id": "C401"}, by="id")
