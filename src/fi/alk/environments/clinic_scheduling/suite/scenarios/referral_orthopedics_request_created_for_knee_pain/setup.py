def setup(world):
    world.put("patients", {
        "patient_id": "P4012",
        "full_name": "Declan Murphy",
        "dob": "1982-04-12",
        "email": "declan.murphy@example.com",
        "phone": "+447700900412",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P4012",
        "payer": "Bupa",
        "member_id": "BU-100412",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
