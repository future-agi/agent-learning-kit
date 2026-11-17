def setup(world):
    world.put("customers", {"customer_id": "C370", "first_name": "Martin", "last_name": "Kovacs", "email": "martin.kovacs@example.com", "phone": "+1555123370", "passcode": "5533", "kba_answer": "Pepper", "otp_code": "892047", "address": "370 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC370", "customer_id": "C370", "account_number": "1000000370", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD370", "account_id": "ACC370", "customer_id": "C370", "card_number_masked": "••••••••••••5656", "last_four": "5656", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS370", "customer_id": "C370", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 3, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
