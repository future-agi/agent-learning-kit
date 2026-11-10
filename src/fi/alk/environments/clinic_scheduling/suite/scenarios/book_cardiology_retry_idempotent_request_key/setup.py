def setup(world):
    world.put("patients", {
        "patient_id": "P411",
        "full_name": "Oliver Higgins",
        "dob": "1950-10-10",
        "email": "oliver.higgins@example.com",
        "phone": "+447700900411",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P411",
        "payer": "Bupa",
        "member_id": "BU-100241",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
    world.put("referrals", {
        "referral_id": "RF411",
        "patient_id": "P411",
        "specialty": "cardiology",
        "status": "active",
        "referring_practice": "St Jude Practice",
        "expires_on": "2027-05-01",
        "request_key": None,
        "created_at": "2026-10-01T10:00:00Z",
        "updated_at": "2026-10-01T10:00:00Z",
    })
