def setup(world):
    world.put("employees", {
        "employee_id": "E420",
        "name": "Hamish Clarke",
        "email": "hamish.clarke@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "software_engineer"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E420"}, by="id")
