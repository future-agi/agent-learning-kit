def setup(world):
    world.put("patients", {"patient_id": "P413", "full_name": "Lucia Bianchi", "dob": "1996-06-06", "phone": "+447700900413", "email": "lucia.bianchi@example.com", "guardian_name": None, "is_new_patient": 0})
    world.put("insurance", {"patient_id": "P413", "payer": "Vitality", "member_id": "VT-990413", "group_number": "", "updated_at": "2026-01-05T09:00:00Z"})
    world.change("session_caller", "default", {"caller_id": "P413"}, by="id")
