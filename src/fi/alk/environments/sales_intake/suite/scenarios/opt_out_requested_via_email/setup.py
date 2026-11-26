def setup(world):
    world.put("gmail_threads", {
        "id": "th_s406_optout",
        "history_id": "shist_406",
        "subject": "Please record my opt out from BrightDesk outreach",
        "snippet": "Please record my opt out and remove me from all BrightDesk emails and calls. George",
        "is_restricted": 0,
        "updated_at": "2026-11-09T11:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_407",
        "thread_id": "th_s406_optout",
        "sender": "george.whitaker@harbourline.example",
        "recipient": "sales@brightdesk.example",
        "subject": "Please record my opt out from BrightDesk outreach",
        "snippet": "Please record my opt out and remove me from all BrightDesk emails and calls. George",
        "body": "Hi BrightDesk team, please record my status as opted_out and remove me from all future emails, calls, and follow-ups. George Whitaker",
        "date": "2026-11-09T11:00:00Z",
        "cc": ""
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s406_optout",
        "label_id": "INBOX"
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "C25"}, by="id")
