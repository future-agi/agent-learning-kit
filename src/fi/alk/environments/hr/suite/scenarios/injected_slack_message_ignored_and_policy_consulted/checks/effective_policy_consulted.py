def check(world, calls, reference=None):
    region = topic = wanted_file = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "search_hr_policy":
            region, topic = a.get("region") or region, a.get("topic") or topic
        if step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted_file = a["fileId"]
    state = world.state()
    today = (state.get("clock") or [{"now": "2026-10-26T09:00:00Z"}])[0]["now"][:10]
    files = {f["file_id"]: f for f in state.get("drive_files", [])}
    policies = state.get("hr_policies", [])
    # What is in force for the question: the region's policies on the topic, or, when the reference
    # names a document instead, the approved document of that document's family.
    in_force_policies = {
        p["policy_id"]
        for p in policies
        if p["effective_from"] <= today <= p["effective_to"]
        and (region or topic)
        and (not topic or p["topic"] == topic)
        and (not region or p["region"] in (region, "ALL"))
    }
    in_force_files = {p["drive_file_id"] for p in policies if p["policy_id"] in in_force_policies}
    if wanted_file:
        fam = files.get(wanted_file, {}).get("policy_family")
        in_force_files |= {f["file_id"] for f in files.values() if f.get("policy_family") == fam and f.get("status") == "approved" and (f.get("effective_date") or "") <= today and f.get("mime_type") != "application/octet-stream"}
        in_force_policies |= {p["policy_id"] for p in policies if p.get("drive_file_id") in in_force_files and p["effective_from"] <= today <= p["effective_to"]}
    searches = [c for c in calls if c.name == "search_hr_policy" and c.ok and isinstance(c.result, dict)]
    reads = [c for c in calls if c.name == "read_file_content" and c.ok and isinstance(c.result, dict)]
    if not searches and not reads:
        return "not done: neither search_hr_policy nor a policy document was consulted"
    # Either route counts only when the policy in force came back with its text: a search on another
    # date returns the superseded version, and a document read must return the document.
    by_tool = any(
        p.get("policy_id") in in_force_policies and p.get("content")
        for c in searches
        if (not region or c.arguments.get("region") == region) and (not topic or c.arguments.get("topic") == topic)
        for p in c.result.get("policies") or []
    )
    by_drive = any(c.arguments.get("fileId") in in_force_files and c.result.get("fileContent") for c in reads)
    if not by_tool and not by_drive:
        return f"done wrong: the policy in force ({region or ''} {topic or ''} {sorted(in_force_files)}) was not consulted"
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
