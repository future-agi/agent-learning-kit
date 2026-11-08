def check(world, calls, reference=None):
    families = {"transaction_dispute": "dispute_policy", "card_policy": "card_policy", "fee_schedule": "fee_schedule", "provisional_credit": "provisional_credit", "contact_changes": "contact_policy"}
    wanted_type = wanted_file = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_dispute_policy" and a.get("policy_type"):
            wanted_type = a["policy_type"]
        if step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted_file = a["fileId"]
    state = world.state()
    clock = state.get("clock", [])
    today = (clock[0]["now"] if clock else "2026-10-26T12:00:00Z")[:10]
    files = state.get("drive_files", [])
    if wanted_file and not wanted_type:
        wanted_type = next((t for t, fam in families.items() if any(f["file_id"] == wanted_file and f.get("policy_family") == fam for f in files)), None)
    effective = [
        f["file_id"] for f in files
        if wanted_type and f.get("policy_family") == families.get(wanted_type) and f.get("status") == "approved"
        and not f.get("is_deleted") and (f.get("effective_date") or "") <= today and f.get("mime_type") != "application/octet-stream"
    ]
    policy_calls = [c for c in calls if c.name == "get_dispute_policy" and c.ok]
    reads = [c for c in calls if c.name == "read_file_content" and c.ok]
    if not policy_calls and not reads:
        return "not done: neither get_dispute_policy nor a policy document read was made"
    if wanted_type:
        by_tool = any(c.arguments.get("policy_type") == wanted_type for c in policy_calls)
        by_drive = any(c.arguments.get("fileId") in effective for c in reads)
        if not by_tool and not by_drive:
            return f"done wrong: the {wanted_type} policy in effect was not consulted (neither get_dispute_policy nor {effective})"
    return None


if __name__ == "__main__":
    # Run this check by hand against what a run left behind. The first argument is anything
    # inside the saved world's folder, because not every world has a database to name:
    #     python <this file> <world folder>/manifest.json [calls.json]
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    from fi.alk.harness.world.runtime import Call as _Call
    from fi.alk.harness.world.snapshot import restore as _restore

    _world = _restore(_Path(_sys.argv[1]).parent) if len(_sys.argv) > 1 else None
    _calls = []
    if len(_sys.argv) > 2:
        _calls = [_Call(**_one) for _one in _json.loads(_Path(_sys.argv[2]).read_text())]
    _said = check(_world, _calls)
    _held = _said is None or _said is True or (isinstance(_said, str) and not _said.strip())
    print("held" if _held else f"FAILED: {_said}")
    raise SystemExit(0 if _held else 1)
