def setup(world):
    world.change("session_caller", "default", {"employee_id": "E16"}, by="id")
    world.put("knowledge_requests", {
        "request_id": "KR-410",
        "requester_id": "E16",
        "question": "Is airport parking reimbursed on business trips?",
        "collection_id": "C_PEOPLE",
        "assigned_to": "E03",
        "status": "open",
        "notes": "",
        "request_key": "KR-SEED-410",
        "created_at": "2026-11-05T09:00:00Z",
        "updated_at": "2026-11-05T09:00:00Z"
    })