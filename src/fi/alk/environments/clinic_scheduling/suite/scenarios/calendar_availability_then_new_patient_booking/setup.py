def setup(world):
    world.put("patients", {
        "patient_id": "P22",
        "full_name": "Callum Davies",
        "dob": "1992-10-02",
        "phone": "+447700900122",
        "email": "callum.davies@example.com",
        "guardian_name": "Rachel Davies",
        "is_new_patient": 1
    })
    world.put("insurance", {
        "patient_id": "P22",
        "payer": "Bupa",
        "member_id": "BU-998877",
        "group_number": "RB-01",
        "updated_at": "2026-10-01T09:00:00Z"
    })
    world.put("callers", {"caller_id": "C401", "full_name": "Rachel Davies", "organisation": None})
    world.put("caller_access", {"caller_id": "C401", "patient_id": "P22", "relationship": "wife, registered proxy"})
    world.change("session_caller", "default", {"caller_id": "C401"}, by="id")
