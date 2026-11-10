def setup(world):
    world.put("patients", {
        "patient_id": "P401",
        "full_name": "Gemma O'Connor",
        "dob": "1995-08-20",
        "email": "gemma.oconnor@example.com",
        "phone": "+447700900401",
        "is_new_patient": 1,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P401",
        "payer": "Bupa",
        "member_id": "BU-401921",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
