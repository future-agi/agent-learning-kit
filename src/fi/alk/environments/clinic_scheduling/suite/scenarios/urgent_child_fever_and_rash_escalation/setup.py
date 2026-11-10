def setup(world):
    world.put("patients", {
        "patient_id": "P405",
        "full_name": "Ethan Davies",
        "dob": "2020-03-12",
        "phone": "+447700900405",
        "email": "claire.davies@example.com",
        "guardian_name": "Claire Davies",
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P405",
        "payer": "Bupa",
        "member_id": "BU-405991",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
