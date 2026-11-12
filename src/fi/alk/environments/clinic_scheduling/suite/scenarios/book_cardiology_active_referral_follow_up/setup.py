def setup(world):
    world.put("patients", {
        "patient_id": "P082",
        "full_name": "Efua Mensah",
        "dob": "1979-03-22",
        "phone": "+447700900182",
        "email": "efua.mensah@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P082",
        "payer": "AXA Health",
        "member_id": "AX-200418",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
    world.put("referrals", {
        "referral_id": "RF012",
        "patient_id": "P082",
        "specialty": "cardiology",
        "referring_practice": "Harbour Family Practice",
        "status": "active",
        "expires_on": "2027-04-30",
        "request_key": None,
        "created_at": "2026-09-14T10:00:00Z",
        "updated_at": "2026-09-16T10:00:00Z"
    })
    world.change("session_caller", "default", {"caller_id": "P082"}, by="id")
