def setup(world):
    world.change("session_caller", "default", {"employee_id": "E13"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-412", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:189c52f3c5f5a6e61f019da259c445f659c92b49f1380c9e2383b8ca2b3d901d", "approved_text": "Under precedence rule PR-01, Software Approval Standard 1.0 section 1 prevails over the Procurement Policy: software purchases over $1,000 require an IT security review, so a $1,500 software purchase requires review.", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
