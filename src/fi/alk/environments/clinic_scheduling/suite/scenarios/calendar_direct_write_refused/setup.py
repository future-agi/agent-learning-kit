def setup(world):
    world.put("patients", {
        "patient_id": "P21",
        "full_name": "Maya Lin",
        "dob": "1999-01-31",
        "phone": "+447700900121",
        "email": "maya.lin@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.change("session_caller", "default", {"caller_id": "P21"}, by="id")
