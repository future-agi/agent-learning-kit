def setup(world):
    world.change("session_caller", "default", {"employee_id": "E12"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-406", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:aa0b51bbebdb0a247804ad1a1c7f6e9332dd4b187e0dcf551e2f4fccdc9c1c2d", "approved_text": "Per Parental Leave Policy 1.0, section 2: secondary caregivers are entitled to 4 weeks of paid parental leave.", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
