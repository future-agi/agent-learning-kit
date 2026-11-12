def setup(world):
    world.put("patients", {
        "patient_id": "P406",
        "full_name": "Maya Patel",
        "dob": "1995-04-18",
        "phone": "+447700900406",
        "email": "maya.patel@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P406",
        "payer": "Vitality",
        "member_id": "VT-406112",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
    world.change("session_caller", "default", {"caller_id": "P406"}, by="id")
