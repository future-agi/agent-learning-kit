def setup(world):
    world.put("customers", {"customer_id": "C365", "first_name": "Irene", "last_name": "Kowalski", "email": "irene.kowalski@example.com", "phone": "+1555123365", "passcode": "1153", "kba_answer": "Pepper", "otp_code": "407812", "address": "365 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC365", "customer_id": "C365", "account_number": "1000000365", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD365", "account_id": "ACC365", "customer_id": "C365", "card_number_masked": "••••••••••••1212", "last_four": "1212", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS365", "customer_id": "C365", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
