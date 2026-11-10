def setup(world):
    world.put("gmail_threads", {
        "id": "th_s405_alpenwerk",
        "history_id": "shist_405",
        "subject": "Demo time confirmation",
        "snippet": "We would like to confirm the demo on 13 November at 09:00 Zurich time. Lukas",
        "is_restricted": 0,
        "updated_at": "2026-11-09T10:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_406",
        "thread_id": "th_s405_alpenwerk",
        "sender": "lukas.meier@alpenwerk.example",
        "recipient": "sales@brightdesk.example",
        "subject": "Demo time confirmation",
        "snippet": "We would like to confirm the demo on 13 November at 09:00 Zurich time. Lukas",
        "body": "Hi, following up on the available demo times: we would like to confirm the slot on 13 November at 09:00 Zurich time. Lukas Meier",
        "date": "2026-11-09T10:00:00Z",
        "cc": ""
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s405_alpenwerk",
        "label_id": "INBOX"
    })
