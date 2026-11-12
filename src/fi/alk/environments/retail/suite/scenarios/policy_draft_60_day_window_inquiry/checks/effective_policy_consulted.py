def check(world, calls, reference=None):
    wanted_category = wanted_file = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_return_policy" and a.get("category"):
            wanted_category = a["category"]
        if step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted_file = a["fileId"]
    state = world.state()
    today = (state.get("clock") or [{"now": "2026-10-26T12:00:00Z"}])[0]["now"][:10]
    files = {f["file_id"]: f for f in state.get("drive_files", [])}
    family = files.get(wanted_file, {}).get("policy_family") if wanted_file else ("return_policy" if wanted_category else None)
    effective = [f["file_id"] for f in files.values() if family and f.get("policy_family") == family and f.get("status") == "approved" and not f.get("is_deleted") and (f.get("effective_date") or "") <= today and f.get("mime_type") != "application/octet-stream"]
    policy_calls = [c for c in calls if c.name == "get_return_policy" and c.ok]
    # An earlier revision is a working copy, not the document in effect.
    reads = [c for c in calls if c.name == "read_file_content" and c.ok and not c.arguments.get("revisionId")]
    if not policy_calls and not reads:
        return "not done: neither get_return_policy nor a policy document was consulted"
    if family:
        by_tool = family == "return_policy" and any(not wanted_category or str(c.arguments.get("category", "")).lower() == str(wanted_category).lower() for c in policy_calls)
        by_drive = any(c.arguments.get("fileId") in effective for c in reads)
        if not by_tool and not by_drive:
            return f"done wrong: the {family} in effect was not consulted (neither get_return_policy for {wanted_category} nor {effective})"
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
