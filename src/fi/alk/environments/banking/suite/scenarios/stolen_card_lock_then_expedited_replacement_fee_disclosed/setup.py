def setup(world):
    world.put("customers", {"customer_id": "C302", "first_name": "Omar", "last_name": "Siddiqui", "email": "omar.siddiqui@example.com", "phone": "+1555123302", "passcode": "9023", "kba_answer": "Pepper", "otp_code": "615382", "address": "302 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC302", "customer_id": "C302", "account_number": "1000000302", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD302", "account_id": "ACC302", "customer_id": "C302", "card_number_masked": "••••••••••••3382", "last_four": "3382", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS302", "customer_id": "C302", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
