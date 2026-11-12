def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E508", "name": "Ingrid Solberg", "email": "ingrid.solberg@example.com", "department": "onboarding", "manager_id": "E07", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E508"}, by="id")
