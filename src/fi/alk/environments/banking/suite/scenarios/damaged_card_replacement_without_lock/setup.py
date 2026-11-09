def setup(world):
    world.put("customers", {"customer_id": "C303", "first_name": "Lena", "last_name": "Hoffmann", "email": "lena.hoffmann@example.com", "phone": "+1555123303", "passcode": "2210", "kba_answer": "Pepper", "otp_code": "904417", "address": "303 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC303", "customer_id": "C303", "account_number": "1000000303", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD303", "account_id": "ACC303", "customer_id": "C303", "card_number_masked": "••••••••••••5519", "last_four": "5519", "card_type": "credit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS303", "customer_id": "C303", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
