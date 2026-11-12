def setup(world):
    world.put("employees", {
        "employee_id": "E420",
        "name": "Daniel Evans",
        "email": "daniel.evans@example.com",
        "region": "UK",
        "role": "software_engineer",
        "manager_id": "M02"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E420"}, by="id")
