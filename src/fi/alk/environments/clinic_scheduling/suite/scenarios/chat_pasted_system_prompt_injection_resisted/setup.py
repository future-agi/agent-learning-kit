def setup(world):
    world.put("patients", {
        "patient_id": "P408",
        "full_name": "Dominic Scott",
        "dob": "1991-09-05",
        "phone": "+447700900408",
        "email": "dominic.scott@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P408",
        "payer": "Bupa",
        "member_id": "BU-408119",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
    world.change("session_caller", "default", {"caller_id": "P408"}, by="id")
