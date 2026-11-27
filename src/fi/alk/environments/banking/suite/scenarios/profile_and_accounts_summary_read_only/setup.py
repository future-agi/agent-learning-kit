def setup(world):
    world.put("customers", {"customer_id": "C366", "first_name": "Jonas", "last_name": "Weber", "email": "jonas.weber@example.com", "phone": "+1555123366", "passcode": "4487", "kba_answer": "Pepper", "otp_code": "730159", "address": "366 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC366", "customer_id": "C366", "account_number": "1000000366", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD366", "account_id": "ACC366", "customer_id": "C366", "card_number_masked": "••••••••••••4518", "last_four": "4518", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD366", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("cards", {"card_id": "CARD367", "account_id": "ACC366", "customer_id": "C366", "card_number_masked": "••••••••••••6189", "last_four": "6189", "card_type": "credit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD367", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS366", "customer_id": "C366", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
    world.change("session_caller", "default", {"customer_id": "C366", "staff_id": None}, by="id")
