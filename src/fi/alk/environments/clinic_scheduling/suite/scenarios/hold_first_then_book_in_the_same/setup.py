def setup(world):
    world.put("patients", {
        "patient_id": "P421",
        "full_name": "Tomasz Nowak",
        "dob": "1983-11-21",
        "phone": "+447700900421",
        "email": "tomasz.nowak@example.com",
        "is_new_patient": 1,
        "guardian_name": None
    })
    world.put("insurance", {
        "patient_id": "P421",
        "payer": "Vitality",
        "member_id": "VT-300421",
        "group_number": "",
        "updated_at": "2026-10-26T09:00:00Z"
    })
    world.change("session_caller", "default", {"caller_id": "P421"}, by="id")
