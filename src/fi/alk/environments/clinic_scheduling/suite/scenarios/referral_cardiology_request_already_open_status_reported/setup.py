def setup(world):
    world.put("patients", {
        "patient_id": "P402",
        "full_name": "Pooja Banerjee",
        "dob": "1990-11-03",
        "email": "pooja.banerjee@example.com",
        "phone": "+447700900402",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P402",
        "payer": "AXA Health",
        "member_id": "AX-200402",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
    world.put("referrals", {
        "referral_id": "RF402",
        "patient_id": "P402",
        "specialty": "cardiology",
        "status": "requested",
        "referring_practice": "St Mary Medical",
        "created_at": "2026-10-25T10:00:00Z",
        "updated_at": "2026-10-25T10:00:00Z",
        "expires_on": None,
        "request_key": "seed-RF402",
    })
    world.change("session_caller", "default", {"caller_id": "P402"}, by="id")
