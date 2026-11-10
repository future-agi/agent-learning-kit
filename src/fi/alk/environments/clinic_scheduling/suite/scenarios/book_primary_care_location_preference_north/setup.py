def setup(world):
    world.put("patients", {
        "patient_id": "P101",
        "full_name": "Greta Schmidt",
        "dob": "1993-08-15",
        "phone": "+447700900110",
        "email": "greta.schmidt@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P101",
        "payer": "Aviva",
        "member_id": "AV-400121",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
