def setup(world):
    world.put("customers", {"customer_id": "C309", "first_name": "Beatriz", "last_name": "Costa", "email": "beatriz.costa@example.com", "phone": "+1555123309", "passcode": "5502", "kba_answer": "Pepper", "otp_code": "396041", "address": "309 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC309", "customer_id": "C309", "account_number": "1000000309", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD309", "account_id": "ACC309", "customer_id": "C309", "card_number_masked": "••••••••••••9174", "last_four": "9174", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD309", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS309", "customer_id": "C309", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
