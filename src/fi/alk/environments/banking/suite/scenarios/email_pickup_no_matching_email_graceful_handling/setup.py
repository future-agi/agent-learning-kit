def setup(world):
    world.put("sessions", {"session_id": "SESS104", "customer_id": "C04", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    # The premise is that Diana's email never arrived. The base mailbox holds her D77 email about
    # this same charge (th_c04_claims_unverified, which the claimed-dispute scenario uses), so it
    # is removed here, for this scenario only; her unrelated Zara thread stays as a distractor.
    world.drop("gmail_messages", "th_c04_claims_unverified", by="thread_id")
    world.drop("gmail_thread_labels", "th_c04_claims_unverified", by="thread_id")
    world.drop("gmail_threads", "th_c04_claims_unverified", by="id")
    world.change("session_caller", "default", {"customer_id": "C04", "staff_id": None}, by="id")
