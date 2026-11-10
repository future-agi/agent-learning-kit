def setup(world):
    world.put("patients", {
        "patient_id": "P21",
        "full_name": "Mateo Silva",
        "dob": "1985-05-15",
        "phone": "+447700900121",
        "email": "mateo.silva@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("referrals", {
        "referral_id": "RF21",
        "patient_id": "P21",
        "specialty": "orthopedics",
        "status": "pending",
        "referring_practice": "Westside Medical Centre",
        "expires_on": None,
        "request_key": "seed-RF21",
        "created_at": "2026-10-23T10:00:00Z",
        "updated_at": "2026-10-23T10:00:00Z"
    })
