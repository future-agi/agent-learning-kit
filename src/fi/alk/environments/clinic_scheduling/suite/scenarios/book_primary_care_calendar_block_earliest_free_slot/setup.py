def setup(world):
    world.put("patients", {
        "patient_id": "P4011",
        "full_name": "Daniel Vance",
        "dob": "1980-06-18",
        "email": "daniel.vance@example.com",
        "phone": "+447700900411",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P4011",
        "payer": "Bupa",
        "member_id": "BU-100411",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
