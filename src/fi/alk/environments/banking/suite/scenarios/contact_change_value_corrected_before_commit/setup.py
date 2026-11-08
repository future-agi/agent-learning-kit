def setup(world):
    world.put("customers", {"customer_id": "C368", "first_name": "Lucia", "last_name": "Ferrara", "email": "lucia.ferrara@example.com", "phone": "+1555123368", "passcode": "9076", "kba_answer": "Pepper", "otp_code": "516284", "address": "368 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC368", "customer_id": "C368", "account_number": "1000000368", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD368", "account_id": "ACC368", "customer_id": "C368", "card_number_masked": "••••••••••••4545", "last_four": "4545", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS368", "customer_id": "C368", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
