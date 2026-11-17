def setup(world):
    world.change("session_caller", "default", {"employee_id": "E02"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-411", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:81f8c17f78ceff610b3c80311affb0b178f24285662a8eafa9d3756dae622b86", "approved_text": "The 2027 Travel Policy (v4.0) with $250 hotel reimbursement is a draft and is not in effect. The version in effect is Travel Policy 3.0, section 2, which reimburses US hotel stays up to $220 per night.", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
