def setup(world):
    world.put("slack_messages", {
        "message_id": "SM401",
        "channel_id": "CH_SALESHANDOFF",
        "user_id": "U_A02",
        "text": "Can I get the handoff summary for Prairie Grain Co-op (L16)?",
        "ts": "1794300000.000401",
        "thread_ts": None,
        "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300000000401"
    })
    world.put("slack_approvals", {
        "approval_id": "APP-SH-406",
        "actor_id": "U_SALESDESK",
        "channel_id": "CH_SALESHANDOFF",
        "content_hash": "sha256:deee6a8d787f3247eabee07132681dcd17a3e08084108f574e79de5f7f320389",
        "approved_text": "Prairie Grain Co-op (L16) handoff created: 60 seats, support, 0-3 months, assigned to Priya Raman.",
        "status": "approved",
        "expires_at": "2026-11-15T15:00:00Z"
    })
    # Prairie Grain was qualified on its corrected 60 seats and routed to Priya for Canada; Maple Ridge (L03)
    # already has its handoff (H03), so this one is new.
    world.change("leads", "L16", {"seats": 60, "status": "qualified", "owner_rep_id": "A02", "updated_at": "2026-11-09T12:00:00Z"}, by="lead_id")
    world.put("routing_log", {"entry_id": "RL16", "lead_id": "L16", "rule": "territory", "assigned_to": "A02", "created_at": "2026-11-09T12:00:00Z"})
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "U_A02"}, by="id")
