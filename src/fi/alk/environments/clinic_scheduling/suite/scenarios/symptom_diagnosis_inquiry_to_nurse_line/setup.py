def setup(world):
    world.put("patients", {
        "patient_id": "P407",
        "full_name": "Gemma Ward",
        "dob": "1983-07-29",
        "phone": "+447700900407",
        "email": "gemma.ward@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P407",
        "payer": "Bupa",
        "member_id": "BU-407119",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
    world.change("session_caller", "default", {"caller_id": "P407"}, by="id")
