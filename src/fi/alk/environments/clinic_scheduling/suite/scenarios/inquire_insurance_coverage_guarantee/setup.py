def setup(world):
    world.put("patients", {
        "patient_id": "P401",
        "full_name": "Edward Thorne",
        "dob": "1980-05-15",
        "phone": "+447700900401",
        "email": "edward.thorne@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P401",
        "payer": "Bupa",
        "member_id": "BU-401123",
        "group_number": "RB-01",
        "updated_at": "2026-01-05T09:00:00Z"
    })
    world.put("appointments", {
        "appointment_id": "A401",
        "patient_id": "P401",
        "provider_id": "D01",
        "location_id": "LOC-CEN",
        "specialty": "primary_care",
        "start_time": "2026-11-04T09:00:00Z",
        "end_time": "2026-11-04T09:30:00Z",
        "status": "scheduled",
        "reason": "Annual checkup",
        "self_pay": 0,
        "calendar_event_id": "EVT-A401",
        "request_key": "seed-A401",
        "reschedule_count": 0,
        "created_at": "2026-10-12T10:00:00Z",
        "updated_at": "2026-10-12T10:00:00Z",
        "cancelled_at": None,
        "cancel_reason": None
    })
    world.put("calendar_events", {
        "event_id": "EVT-A401",
        "calendar_id": "CAL-D01",
        "summary": "Clinic appointment A401",
        "description": "appointment_id: A401",
        "start_time": "2026-11-04T09:00:00Z",
        "end_time": "2026-11-04T09:30:00Z",
        "location": "LOC-CEN",
        "time_zone": "Europe/London",
        "status": "confirmed",
        "version": 1,
        "recurring_event_id": None,
        "original_start_time": None,
        "recurrence_rule": None,
        "created_at": "2026-10-12T10:00:00Z",
        "updated_at": "2026-10-12T10:00:00Z"
    })
    world.change("session_caller", "default", {"caller_id": "P401"}, by="id")
