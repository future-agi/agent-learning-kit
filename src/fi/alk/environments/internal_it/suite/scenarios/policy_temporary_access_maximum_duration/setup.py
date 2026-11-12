def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E510", "name": "Anika Rao", "email": "anika.rao@example.com", "department": "operations", "manager_id": "E07", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E510"}, by="id")
