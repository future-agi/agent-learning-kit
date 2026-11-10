def setup(world):
    world.put("gmail_threads", {
        "history_id": "bhist_s401",
        "id": "th_s401_upgrade",
        "is_restricted": 0,
        "snippet": "LunarOps wants to upgrade to Enterprise with 25 seats at our next renewal.",
        "subject": "Upgrade to Enterprise at renewal",
        "updated_at": "2026-10-14T15:00:00Z"
    })
    world.put("gmail_messages", {
        "body": "Hi Billing Desk, LunarOps (T12) would like to upgrade to Enterprise with 25 seats at our next renewal. Please schedule this. - Leo Lunar, Billing Admin",
        "cc": "",
        "date": "2026-10-14T15:00:00Z",
        "id": "smsg_401",
        "recipient": "billing@cloudnest.example",
        "sender": "leo@lunarops.com",
        "snippet": "LunarOps wants to upgrade to Enterprise with 25 seats at our next renewal.",
        "subject": "Upgrade to Enterprise at renewal",
        "thread_id": "th_s401_upgrade"
    })
    world.put("gmail_thread_labels", {"thread_id": "th_s401_upgrade", "label_id": "INBOX"})
    world.put("gmail_thread_labels", {"thread_id": "th_s401_upgrade", "label_id": "billing"})