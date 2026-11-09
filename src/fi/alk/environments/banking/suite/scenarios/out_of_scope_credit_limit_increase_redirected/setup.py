def setup(world):
    world.put("customers", {"customer_id": "C380", "first_name": "Rita", "last_name": "Menon", "email": "rita.menon@example.com", "phone": "+1555123380", "passcode": "7719", "kba_answer": "Pepper", "otp_code": "463082", "address": "380 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC380", "customer_id": "C380", "account_number": "1000000380", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD380", "account_id": "ACC380", "customer_id": "C380", "card_number_masked": "••••••••••••8989", "last_four": "8989", "card_type": "credit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS380", "customer_id": "C380", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
