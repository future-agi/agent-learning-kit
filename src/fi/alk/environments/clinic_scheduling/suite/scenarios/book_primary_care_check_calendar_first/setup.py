def setup(world):
    world.put("patients", {
        "patient_id": "P407",
        "full_name": "Arthur Pendelton",
        "dob": "1958-09-15",
        "email": "arthur.pendelton@example.com",
        "phone": "+447700900407",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P407",
        "payer": "Bupa",
        "member_id": "BU-100407",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
