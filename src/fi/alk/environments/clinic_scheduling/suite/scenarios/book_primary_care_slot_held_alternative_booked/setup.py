def setup(world):
    world.put("patients", {
        "patient_id": "P414",
        "full_name": "Sean Connolly",
        "dob": "1966-04-30",
        "email": "sean.connolly@example.com",
        "phone": "+447700900414",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P414",
        "payer": "Vitality",
        "member_id": "VT-300124",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
