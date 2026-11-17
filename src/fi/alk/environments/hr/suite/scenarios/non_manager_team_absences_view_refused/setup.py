def setup(world):
    world.put("employees", {
        "employee_id": "E416",
        "name": "Siddharth Rao",
        "email": "siddharth.rao@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "data_analyst"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E416"}, by="id")
