def setup(world):
    world.put("customers", {"customer_id": "C362", "first_name": "Diego", "last_name": "Moreno", "email": "diego.moreno@example.com", "phone": "+1555123362", "passcode": "3302", "kba_answer": "Pepper", "otp_code": "958147", "address": "362 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC362", "customer_id": "C362", "account_number": "1000000362", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD362", "account_id": "ACC362", "customer_id": "C362", "card_number_masked": "••••••••••••7272", "last_four": "7272", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS362", "customer_id": "C362", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
