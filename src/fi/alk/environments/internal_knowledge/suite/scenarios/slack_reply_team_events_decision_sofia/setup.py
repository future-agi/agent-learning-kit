def setup(world):
    world.change("session_caller", "default", {"employee_id": "E06"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-403", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:a1ae3e10d769677b6877cae0cbaffab275ef33107362bf249024911e88b09da8", "approved_text": "While Team Events Budget 1.0 section 1 states $100 per head, Tomas Rivera posted a decision in #finance-team on 2026-10-28 raising the team events cap to $150 per head from 1 November (document update pending).", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
