def setup(world):
    world.change("session_caller", "default", {"employee_id": "E16"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-413", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:e8aa1663434eb19cfe9da9ed4b4bd76236c8a826ed1f43c99ac77fe850969298", "approved_text": "Per Meal Per Diem Standard 2.0, section 2: alcohol is not reimbursable under the meal per diem. Client entertainment expenses are handled under a separate policy and budget.", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
