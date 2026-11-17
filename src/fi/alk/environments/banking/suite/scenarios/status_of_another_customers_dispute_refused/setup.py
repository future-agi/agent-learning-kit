def setup(world):
    world.put("customers", {"customer_id": "C345", "first_name": "Walter", "last_name": "Brennan", "email": "walter.brennan@example.com", "phone": "+1555123345", "passcode": "5570", "kba_answer": "Pepper", "otp_code": "813064", "address": "345 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC345", "customer_id": "C345", "account_number": "1000000345", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD345", "account_id": "ACC345", "customer_id": "C345", "card_number_masked": "••••••••••••6161", "last_four": "6161", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS345", "customer_id": "C345", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
