def setup(world):
    world.put("customers", {"customer_id": "C312", "first_name": "Felix", "last_name": "Nowak", "email": "felix.nowak@example.com", "phone": "+1555123312", "passcode": "9961", "kba_answer": "Pepper", "otp_code": "572803", "address": "312 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC312", "customer_id": "C312", "account_number": "1000000312", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD312", "account_id": "ACC312", "customer_id": "C312", "card_number_masked": "••••••••••••1010", "last_four": "1010", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD312", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("cards", {"card_id": "CARD313", "account_id": "ACC312", "customer_id": "C312", "card_number_masked": "••••••••••••2020", "last_four": "2020", "card_type": "credit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD313", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS312", "customer_id": "C312", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
    world.change("session_caller", "default", {"customer_id": "C312", "staff_id": None}, by="id")
