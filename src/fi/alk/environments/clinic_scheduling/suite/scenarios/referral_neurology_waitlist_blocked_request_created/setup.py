def setup(world):
    world.put("patients", {
        "patient_id": "P410",
        "full_name": "Chloe Tremblay",
        "dob": "1993-08-15",
        "email": "chloe.tremblay@example.com",
        "phone": "+447700900410",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P410",
        "payer": "Aviva",
        "member_id": "AV-400110",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
    world.put("referrals", {
        "referral_id": "RF410",
        "patient_id": "P410",
        "specialty": "neurology",
        "status": "expired",
        "referring_practice": "Canonbury Health Centre",
        "expires_on": "2026-10-01",
        "request_key": None,
        "created_at": "2026-04-01T10:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
    })
