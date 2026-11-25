def setup(world):
    world.put("customers", {"customer_id": "C107", "first_name": "Victor", "last_name": "Chen", "email": "victor.c@example.com", "phone": "+15551234107", "passcode": "6732", "kba_answer": "Lucy", "otp_code": "284019", "address": "107 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC107", "customer_id": "C107", "account_number": "1000000107", "account_type": "checking", "balance": 550000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD107", "account_id": "ACC107", "customer_id": "C107", "card_number_masked": "••••••••••••5096", "last_four": "5096", "card_type": "debit", "status": "active", "expiration_date": "2028-02-28", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD107", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS107", "customer_id": "C107", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    world.change("session_caller", "default", {"customer_id": "C107", "staff_id": None}, by="id")
