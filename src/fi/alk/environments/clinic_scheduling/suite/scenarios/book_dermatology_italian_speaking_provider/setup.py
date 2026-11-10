def setup(world):
    world.put("patients", {
        "patient_id": "P131",
        "full_name": "Alessia DeLuca",
        "dob": "1996-06-06",
        "phone": "+447700900113",
        "email": "alessia.deluca@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P131",
        "payer": "Vitality",
        "member_id": "VT-300128",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
