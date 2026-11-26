def setup(world):
    world.change("session_caller", "default", {"employee_id": "E03"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-401", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:bc3e911ae381c04210b0d6d8d32cdad4909821b4dcedd52f02fda1f3598252a5", "approved_text": "Per the On-call Policy 2.0, section 1: on-call engineers receive $400 a week, plus $50 for each after-hours page (section 2).", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
