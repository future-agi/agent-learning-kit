def setup(world):
    world.put("patients", {
        "patient_id": "P420",
        "full_name": "Kofi Asante",
        "dob": "1987-05-19",
        "phone": "+447700900420",
        "email": "kofi.asante@example.com",
        "is_new_patient": 1,
        "guardian_name": None
    })
    world.put("insurance", {
        "patient_id": "P420",
        "payer": "WPA",
        "member_id": "WP-500420",
        "group_number": "",
        "updated_at": "2026-10-26T09:00:00Z"
    })
    world.change("session_caller", "default", {"caller_id": "P420"}, by="id")
