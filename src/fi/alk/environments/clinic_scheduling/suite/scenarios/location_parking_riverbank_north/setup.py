def setup(world):
    world.put("patients", {
        "patient_id": "P402",
        "full_name": "Leo Cooper",
        "dob": "2019-08-10",
        "phone": "+447700900402",
        "email": "rachel.cooper@example.com",
        "guardian_name": "Rachel Cooper",
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P402",
        "payer": "Vitality",
        "member_id": "VT-402123",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
