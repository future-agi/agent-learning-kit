def setup(world):
    world.put("sessions", {"session_id": "SESS108", "customer_id": "C08", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    world.change("session_caller", "default", {"customer_id": "C08", "staff_id": None}, by="id")
