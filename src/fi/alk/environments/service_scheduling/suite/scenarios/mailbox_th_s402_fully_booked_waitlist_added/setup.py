def setup(world):
    world.put("customers", {
        "customer_id": "CU405",
        "name": "Hannah Clarke",
        "email": "hannah.clarke@example.com",
        "phone": "+1 416 555 0405",
        "preferred_language": "en",
        "time_zone": "America/Toronto",
        "created_at": "2026-10-26T09:00:00Z"
    })
    world.put("gmail_threads", {
        "id": "th_s402_full_day_waitlist",
        "subject": "Massage booking inquiry Nov 5",
        "snippet": "Hello, please book me a 60-min Massage Therapy session on Thursday 5 November at Midtown.",
        "history_id": "shist_402",
        "is_restricted": 0,
        "updated_at": "2026-10-25T18:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_402",
        "thread_id": "th_s402_full_day_waitlist",
        "sender": "hannah.clarke@example.com",
        "recipient": "bookings@northlight-studios.example",
        "cc": "",
        "subject": "Massage booking inquiry Nov 5",
        "body": "Hello, please book me a 60-min Massage Therapy session on Thursday 5 November at Midtown. If there are no openings, please add me to the waitlist for that date. Hannah Clarke",
        "date": "2026-10-25T18:00:00Z",
        "snippet": "Hello, please book me a 60-min Massage Therapy session on Thursday 5 November at Midtown."
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s402_full_day_waitlist",
        "label_id": "INBOX"
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s402_full_day_waitlist",
        "label_id": "bookings"
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s402_full_day_waitlist",
        "label_id": "needs_reply"
    })
    for i in range(12):
        hour = 8 + i
        hour_str = f"{hour:02d}:00"
        end_hour = hour + 1
        end_hour_str = f"{end_hour:02d}:00"
        appt_id = f"A4{20+i:02d}"
        ev_id = f"EV-A4{20+i:02d}"
        s_utc_h = hour + 5
        e_utc_h = end_hour + 5
        s_utc = f"2026-11-05T{s_utc_h:02d}:00:00Z" if s_utc_h < 24 else f"2026-11-06T{s_utc_h-24:02d}:00:00Z"
        e_utc = f"2026-11-05T{e_utc_h:02d}:00:00Z" if e_utc_h < 24 else f"2026-11-06T{e_utc_h-24:02d}:00:00Z"
        world.put("appointments", {
            "appointment_id": appt_id,
            "customer_id": "CU01",
            "service_id": "SV2",
            "staff_id": "ST01",
            "location_id": "L1",
            "start_utc": s_utc,
            "end_utc": e_utc,
            "local_start": f"2026-11-05T{hour_str}",
            "utc_offset": "-05:00",
            "time_zone": "America/New_York",
            "status": "booked",
            "calendar_event_id": ev_id,
            "request_key": f"SEED-{appt_id}",
            "rescheduled_from": None,
            "source": "seed",
            "late_fee_cents": 0,
            "created_at": "2026-10-20T12:00:00Z",
            "updated_at": "2026-10-20T12:00:00Z",
            "cancelled_at": None
        })
        world.put("calendar_events", {
            "event_id": ev_id,
            "calendar_id": "CAL-ST01",
            "summary": f"Massage Therapy - Seed {appt_id}",
            "description": f"appointment:{appt_id}",
            "location": "Midtown Studio",
            "start_time": s_utc,
            "end_time": e_utc,
            "time_zone": "America/New_York",
            "status": "confirmed",
            "recurring_event_id": None,
            "recurrence_rule": None,
            "original_start_time": None,
            "version": 1,
            "created_at": "2026-10-20T12:00:00Z",
            "updated_at": "2026-10-20T12:00:00Z"
        })
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU405", "desk_id": None}, by="id")
