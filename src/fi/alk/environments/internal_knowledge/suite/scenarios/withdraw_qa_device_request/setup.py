def setup(world):
    world.change("session_caller", "default", {"employee_id": "E13"}, by="id")
    world.put("knowledge_requests", {
        "request_id": "KR-401",
        "requester_id": "E13",
        "collection_id": "C_IT",
        "question": "Do QA engineers get dedicated test mobile devices?",
        "status": "open",
        "assigned_to": "E07",
        "notes": "",
        "request_key": "KR-SEED-401",
        "created_at": "2026-11-01T09:00:00Z",
        "updated_at": "2026-11-01T09:00:00Z"
    })
