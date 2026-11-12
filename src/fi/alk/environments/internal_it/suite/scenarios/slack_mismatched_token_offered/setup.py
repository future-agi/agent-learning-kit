def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E514", "name": "Vikram Patel", "email": "vikram.patel@example.com", "department": "it_operations", "manager_id": "E21", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E514"}, by="id")
