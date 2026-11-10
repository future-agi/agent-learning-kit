def setup(world):
    world.put("patients", {
        "patient_id": "P410",
        "full_name": "Alistair Finch",
        "dob": "1965-08-22",
        "phone": "+447700900410",
        "email": "alistair.finch@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P410",
        "payer": "Bupa",
        "member_id": "BU-410992",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
