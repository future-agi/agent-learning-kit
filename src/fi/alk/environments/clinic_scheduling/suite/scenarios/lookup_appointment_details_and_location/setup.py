def setup(world):
    world.put("patients", {
        "patient_id": "P403",
        "full_name": "Nathan Brooks",
        "dob": "1988-03-14",
        "phone": "+447700900403",
        "email": "nathan.brooks@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P403",
        "payer": "Bupa",
        "member_id": "BU-403991",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A403",
        "patient_id": "P403",
        "provider_id": "D07",
        "location_id": "LOC-WES",
        "specialty": "physiotherapy",
        "start_time": "2026-11-04T10:30:00Z",
        "end_time": "2026-11-04T11:00:00Z",
        "status": "scheduled",
        "reason": "Knee rehabilitation",
        "self_pay": 0,
        "calendar_event_id": "EVT-A403",
        "request_key": "seed-A403",
        "reschedule_count": 0,
        "created_at": "2026-10-12T10:00:00Z",
        "updated_at": "2026-10-12T10:00:00Z",
        "cancelled_at": None,
        "cancel_reason": None
    })
    world.put("calendar_events", {
        "event_id": "EVT-A403",
        "calendar_id": "CAL-D07",
        "summary": "Clinic appointment A403",
        "description": "appointment_id: A403",
        "start_time": "2026-11-04T10:30:00Z",
        "end_time": "2026-11-04T11:00:00Z",
        "location": "LOC-WES",
        "time_zone": "Europe/London",
        "status": "confirmed",
        "version": 1,
        "recurring_event_id": None,
        "original_start_time": None,
        "recurrence_rule": None,
        "created_at": "2026-10-12T10:00:00Z",
        "updated_at": "2026-10-12T10:00:00Z"
    })
