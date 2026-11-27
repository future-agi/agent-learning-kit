def setup(world):
    world.change("session_caller", "default", {"employee_id": "E15"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-402", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:c38256cc27b6d61788db6243978732d989a467eeaacc51ccb71cdedc04d65c7f", "approved_text": "Hotel stays are reimbursed up to $220 per night in the US and up to $300 per night internationally per Travel Policy 3.0, section 2.", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
