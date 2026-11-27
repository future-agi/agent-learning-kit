def setup(world):
    world.put("patients", {"patient_id": "P410", "full_name": "Greta Weber", "dob": "1993-08-15", "phone": "+447700900410", "email": "greta.weber@example.com", "guardian_name": None, "is_new_patient": 0})
    world.put("insurance", {"patient_id": "P410", "payer": "Aviva", "member_id": "AV-990410", "group_number": "", "updated_at": "2026-01-05T09:00:00Z"})
    world.change("session_caller", "default", {"caller_id": "P410"}, by="id")
