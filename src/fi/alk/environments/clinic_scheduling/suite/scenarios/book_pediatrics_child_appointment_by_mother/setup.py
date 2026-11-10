def setup(world):
    world.put("patients", {
        "patient_id": "P406",
        "full_name": "Leo Davies",
        "dob": "2019-06-12",
        "email": "claire.davies@example.com",
        "phone": "+447700900406",
        "is_new_patient": 0,
        "guardian_name": "Claire Davies",
    })
    world.put("insurance", {
        "patient_id": "P406",
        "payer": "Vitality",
        "member_id": "VT-300406",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
