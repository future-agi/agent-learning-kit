def setup(world):
    world.put("patients", {
        "patient_id": "P409",
        "full_name": "Karan Mehta",
        "dob": "1982-11-14",
        "phone": "+447700900409",
        "email": "karan.mehta@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P409",
        "payer": "Aviva",
        "member_id": "AV-409112",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
    world.change("session_caller", "default", {"caller_id": "P409"}, by="id")
