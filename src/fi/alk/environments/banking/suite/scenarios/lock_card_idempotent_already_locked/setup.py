def setup(world):
    world.put("customers", {"customer_id": "C116", "first_name": "Fiona", "last_name": "MacLeod", "email": "fiona.m@example.co.uk", "phone": "+442079460116", "passcode": "7216", "kba_answer": "Edinburgh", "otp_code": "829104", "address": "116 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC116", "customer_id": "C116", "account_number": "1000000116", "account_type": "checking", "balance": 450000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD116", "account_id": "ACC116", "customer_id": "C116", "card_number_masked": "••••••••••••7216", "last_four": "7216", "card_type": "debit", "status": "locked", "expiration_date": "2027-04-30", "locked_at": "2026-10-25T21:10:00Z"})
    world.put("card_controls", {"card_id": "CARD116", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS116", "customer_id": "C116", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:40:00Z"})
    world.change("session_caller", "default", {"customer_id": "C116", "staff_id": None}, by="id")
