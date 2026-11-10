def setup(world):
    world.put("patients", {
        "patient_id": "P151",
        "full_name": "Lauren Becker",
        "dob": "1988-09-09",
        "phone": "+447700900115",
        "email": "lauren.becker@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P151",
        "payer": "Bupa",
        "member_id": "BU-100245",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
