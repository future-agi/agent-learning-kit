def setup(world):
    world.change("session_caller", "default", {"employee_id": "E03"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-416", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:b367b0259e45adc37933046e0bf3eb9223f24f2f034a2f58809faa520418ce7b", "approved_text": "Salary bands and executive compensation information are restricted and cannot be shared in public channels.", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
