def setup(world):
    world.put("patients", {
        "patient_id": "P418",
        "full_name": "Viktor Novak",
        "dob": "1962-05-17",
        "email": "viktor.novak@example.com",
        "phone": "+447700900418",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P418",
        "payer": "Bupa",
        "member_id": "BU-100248",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
    world.put("referrals", {
        "referral_id": "RF418",
        "patient_id": "P418",
        "specialty": "neurology",
        "status": "active",
        "referring_practice": "Westside Practice",
        "expires_on": "2027-05-01",
        "request_key": None,
        "created_at": "2026-10-01T10:00:00Z",
        "updated_at": "2026-10-01T10:00:00Z",
    })
