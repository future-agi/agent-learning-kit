def setup(world):
    """Another caller books the 10:00 slot the moment Grace confirms it."""
    world.put("clinic_fault_injections", {"fault_id": "FI-401", "operation": "book_appointment", "target_id": "SL-D03-20261027-1000", "fault_type": "slot_taken", "competing_patient_id": "P19", "is_active": 1})
    world.change("session_caller", "default", {"caller_id": "P05"}, by="id")
