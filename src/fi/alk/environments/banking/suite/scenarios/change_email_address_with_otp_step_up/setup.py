def setup(world):
    world.put("customers", {"customer_id": "C361", "first_name": "Clara", "last_name": "Dubois", "email": "clara.dubois@example.com", "phone": "+1555123361", "passcode": "8845", "kba_answer": "Pepper", "otp_code": "173905", "address": "361 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC361", "customer_id": "C361", "account_number": "1000000361", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD361", "account_id": "ACC361", "customer_id": "C361", "card_number_masked": "••••••••••••6161", "last_four": "6161", "card_type": "credit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS361", "customer_id": "C361", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
