def setup(world):
    world.put("slack_approvals", {"approval_id": "APP-SH-401", "actor_id": "U_SALESDESK", "channel_id": "CH_SALESHANDOFF", "content_hash": "sha256:d10cbf336ae42a1b5288267e5a100e6ebb1f77c5647ca40048f3199722360074", "approved_text": "Fernhill Logistics (L08): qualified, customer service, 80 seats, 3-6 months, budget confirmed; owner Oliver Hughes.", "status": "approved", "expires_at": "2026-11-11T15:00:00Z"})
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "U_MGR"}, by="id")
