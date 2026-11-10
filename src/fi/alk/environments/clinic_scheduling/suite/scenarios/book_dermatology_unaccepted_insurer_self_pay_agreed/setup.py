def setup(world):
    world.put("patients", {
        "patient_id": "P161",
        "full_name": "Arjun Shah",
        "dob": "1977-12-12",
        "phone": "+447700900116",
        "email": "arjun.shah@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P161",
        "payer": "Aviva",
        "member_id": "AV-400127",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
