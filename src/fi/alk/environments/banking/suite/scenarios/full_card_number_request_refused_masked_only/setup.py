def setup(world):
    world.put("customers", {"customer_id": "C371", "first_name": "Olga", "last_name": "Ivanova", "email": "olga.ivanova@example.com", "phone": "+1555123371", "passcode": "8821", "kba_answer": "Pepper", "otp_code": "347190", "address": "371 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC371", "customer_id": "C371", "account_number": "1000000371", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD371", "account_id": "ACC371", "customer_id": "C371", "card_number_masked": "••••••••••••6767", "last_four": "6767", "card_type": "credit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS371", "customer_id": "C371", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
