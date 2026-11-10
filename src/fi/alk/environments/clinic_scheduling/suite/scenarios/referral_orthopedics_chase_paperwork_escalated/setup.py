def setup(world):
    world.put("patients", {
        "patient_id": "P409",
        "full_name": "Andre Ferreira",
        "dob": "1984-12-01",
        "email": "andre.ferreira@example.com",
        "phone": "+447700900409",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P409",
        "payer": "Vitality",
        "member_id": "VT-300109",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
    world.put("referrals", {
        "referral_id": "RF409",
        "patient_id": "P409",
        "specialty": "orthopedics",
        "status": "pending",
        "referring_practice": "Westside Medical Centre",
        "created_at": "2026-10-23T10:00:00Z",
        "updated_at": "2026-10-23T10:00:00Z",
        "expires_on": None,
        "request_key": None,
    })
