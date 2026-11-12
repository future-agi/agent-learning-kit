def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E506", "name": "Tobias Lund", "email": "tobias.lund@example.com", "department": "facilities", "manager_id": "E07", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E506"}, by="id")
