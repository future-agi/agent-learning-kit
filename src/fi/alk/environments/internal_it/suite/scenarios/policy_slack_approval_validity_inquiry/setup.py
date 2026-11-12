def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E509", "name": "Lucia Ferraro", "email": "lucia.ferraro@example.com", "department": "customer_service", "manager_id": "E07", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E509"}, by="id")
