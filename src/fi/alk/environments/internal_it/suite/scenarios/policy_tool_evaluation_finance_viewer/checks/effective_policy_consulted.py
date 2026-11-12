def check(world, calls, reference=None):
    wanted_file = None
    policy = {}
    for step in reference or ():
        if step.get("tool") == "read_file_content" and step.get("arguments", {}).get("fileId"):
            wanted_file = step["arguments"]["fileId"]
        if step.get("tool") == "check_access_policy":
            policy = step.get("arguments", {})
    state = world.state()
    today = (state.get("clock") or [{"now": "2026-10-26T09:00:00Z"}])[0]["now"][:10]
    files = {f["file_id"]: f for f in state.get("drive_files", [])}
    fam = files.get(wanted_file, {}).get("policy_family") if wanted_file else "access_policy"
    effective = {f["file_id"] for f in files.values() if f.get("policy_family") == fam and f.get("status") == "approved" and not f.get("is_deleted") and (f.get("effective_date") or "") <= today and f.get("mime_type") != "application/octet-stream"}
    by_drive = any(
        c.name == "read_file_content" and c.ok and c.arguments.get("fileId") in effective
        and isinstance(c.result, dict) and c.result.get("fileContent")
        for c in calls
    )
    # The policy tool answers a question about one employee's role and names the document it is
    # given under; it stands in for reading the document only when the question is that one.
    by_tool = not wanted_file and fam == "access_policy" and any(
        c.name == "check_access_policy" and c.ok and isinstance(c.result, dict)
        and (c.result.get("source") or {}).get("file_id") in effective
        and all(c.arguments.get(k) == v for k, v in policy.items())
        for c in calls
    )
    if not by_tool and not by_drive:
        return f"not done: the {fam} in effect ({sorted(effective)}) was not consulted"
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
