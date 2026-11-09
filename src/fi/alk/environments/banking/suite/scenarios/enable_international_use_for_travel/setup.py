def setup(world):
    world.put("customers", {"customer_id": "C308", "first_name": "Arjun", "last_name": "Mehta", "email": "arjun.mehta@example.com", "phone": "+1555123308", "passcode": "3356", "kba_answer": "Pepper", "otp_code": "447218", "address": "308 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC308", "customer_id": "C308", "account_number": "1000000308", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD308", "account_id": "ACC308", "customer_id": "C308", "card_number_masked": "••••••••••••6063", "last_four": "6063", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD308", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS308", "customer_id": "C308", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
