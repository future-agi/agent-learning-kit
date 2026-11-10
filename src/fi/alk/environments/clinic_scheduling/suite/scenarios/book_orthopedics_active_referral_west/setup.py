def setup(world):
    world.put("patients", {
        "patient_id": "P412",
        "full_name": "Mei Lin Tanaka",
        "dob": "1971-02-27",
        "email": "meilin.tanaka@example.com",
        "phone": "+447700900412",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P412",
        "payer": "AXA Health",
        "member_id": "AX-200412",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
    world.put("referrals", {
        "referral_id": "RF412",
        "patient_id": "P412",
        "specialty": "orthopedics",
        "status": "active",
        "referring_practice": "Harbour Practice",
        "expires_on": "2027-01-01",
        "request_key": None,
        "created_at": "2026-10-01T10:00:00Z",
        "updated_at": "2026-10-01T10:00:00Z",
    })
