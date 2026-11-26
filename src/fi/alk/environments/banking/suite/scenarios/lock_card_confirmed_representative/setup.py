def setup(world):
    world.put("customers", {"customer_id": "C108", "first_name": "Elena", "last_name": "Rostova", "email": "elena.r@example.co.uk", "phone": "+442079460108", "passcode": "6421", "kba_answer": "Oxford", "otp_code": "829103", "address": "108 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC108", "customer_id": "C108", "account_number": "1000000108", "account_type": "checking", "balance": 980000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD108", "account_id": "ACC108", "customer_id": "C108", "card_number_masked": "••••••••••••2671", "last_four": "2671", "card_type": "debit", "status": "active", "expiration_date": "2027-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD108", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS108", "customer_id": "C108", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:00:00Z"})
    world.change("session_caller", "default", {"customer_id": "C108", "staff_id": None}, by="id")
