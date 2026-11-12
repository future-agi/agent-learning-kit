def setup(world):
    world.put("patients", {"patient_id": "P412", "full_name": "Matteo Colombo", "dob": "1996-06-06", "phone": "+447700900412", "email": "matteo.colombo@example.com", "guardian_name": None, "is_new_patient": 0})
    world.put("insurance", {"patient_id": "P412", "payer": "Vitality", "member_id": "VT-990412", "group_number": "", "updated_at": "2026-01-05T09:00:00Z"})
    world.put("slot_holds", {"hold_id": "H401", "patient_id": "P412", "provider_id": "D03", "slot_id": "SL-D03-20261029-1430", "start_time": "2026-10-29T14:30:00Z", "status": "active", "created_at": "2026-10-26T08:55:00Z", "expires_at": "2026-10-26T09:10:00Z", "request_key": "setup-H401", "updated_at": "2026-10-26T08:55:00Z"})
    world.change("session_caller", "default", {"caller_id": "P412"}, by="id")
