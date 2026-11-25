def setup(world):
    # C105 is a customer of its own: no seeded customer shares his surname, email, passcode or card digits.
    # (He used to be a copy of C05 Evan Davis, whose passcode and card ending 7789 verified and locked
    # just as well, so a live agent that mistyped the session landed on C05 and locked the wrong card.)
    world.put("customers", {"customer_id": "C105", "first_name": "Evan", "last_name": "Whitlock", "email": "evan.whitlock@example.com", "phone": "+15551234155", "passcode": "6047", "kba_answer": "Juniper", "otp_code": "318264", "address": "105 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC105", "customer_id": "C105", "account_number": "1000000105", "account_type": "checking", "balance": 480000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD105", "account_id": "ACC105", "customer_id": "C105", "card_number_masked": "••••••••••••4415", "last_four": "4415", "card_type": "debit", "status": "active", "expiration_date": "2027-04-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD105", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS105", "customer_id": "C105", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    world.put("transactions", {"transaction_id": "TX105", "account_id": "ACC105", "card_id": "CARD105", "merchant_name": "Best Buy", "amount": 12000, "currency": "USD", "status": "posted", "transaction_time": "2026-10-25T15:30:00Z", "description": "Electronics purchase"})
    # The partial failure the scenario is about: the dispute service is down for this run only.
    world.put("environment_policy", {"name": "dispute_intake_outage", "value": "true"})
    world.change("session_caller", "default", {"customer_id": "C105", "staff_id": None}, by="id")
