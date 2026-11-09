def setup(world):
    world.put("customers", {"customer_id": "C363", "first_name": "Fatima", "last_name": "Al-Sayed", "email": "fatima.al-sayed@example.com", "phone": "+1555123363", "passcode": "6190", "kba_answer": "Pepper", "otp_code": "284736", "address": "363 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC363", "customer_id": "C363", "account_number": "1000000363", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD363", "account_id": "ACC363", "customer_id": "C363", "card_number_masked": "••••••••••••8383", "last_four": "8383", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS363", "customer_id": "C363", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
