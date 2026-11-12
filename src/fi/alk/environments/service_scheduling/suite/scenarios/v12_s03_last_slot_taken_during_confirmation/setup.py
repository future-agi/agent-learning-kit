def setup(world):
    """Another booking for Marcus at 10:00 New York time on 30 October commits first."""
    world.put('booking_contention', {'contention_id': 'BC401', 'staff_id': 'ST03', 'start_utc': '2026-10-30T14:00:00Z', 'competitor_customer_id': 'CU10', 'status': 'armed'})
    # The person in this conversation, as the session knows them.
    world.change("session_caller", "default", {"customer_id": "CU05", "desk_id": None}, by="id")
