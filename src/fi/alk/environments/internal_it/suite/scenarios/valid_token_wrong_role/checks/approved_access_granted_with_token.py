def check(world, calls, reference=None):
    want_req = want_token = None
    for step in reference or ():
        if step.get("tool") == "grant_synthetic_access":
            want_req = step.get("arguments", {}).get("request_id") or want_req
            want_token = step.get("arguments", {}).get("approval_token") or want_token
    made = [c for c in calls if c.name == "grant_synthetic_access" and c.ok]
    if not made:
        return "not done: grant_synthetic_access was not called successfully"
    state = world.state()
    reqs = {r["request_id"]: r for r in state.get("access_requests", [])}
    for c in made:
        if want_req and c.arguments.get("request_id") != want_req:
            return f"done wrong: access was granted on {c.arguments.get('request_id')}, not {want_req}"
    r = reqs.get(want_req) if want_req else None
    if want_req:
        if not r or r.get("status") != "completed":
            return f"not done: request {want_req} is not completed"
        if not any(e["employee_id"] == r["employee_id"] and e["app_id"] == r["app_id"] and e["role_name"] == r["role_name"] and e["status"] == "active" for e in state.get("entitlements", [])):
            return f"not done: no active entitlement for {want_req}"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The Slack thread or library document the reference fixes, and whether the run retrieved it:
    a direct read, a channel read or search that listed the message, or the document read."""
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("thread", a.get("channel_id"), a["message_ts"]))
        elif step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted.append(("file", None, a["fileId"]))
    missing = []
    for kind, channel, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            if kind == "thread":
                if c.name == "slack_read_thread" and c.arguments.get("message_ts") == target:
                    seen = True
                elif c.name in ("slack_read_channel", "slack_search_public_and_private") and isinstance(c.result, dict) and any(isinstance(m, dict) and (m.get("ts") == target or m.get("thread_ts") == target) for m in c.result.get("messages") or []):
                    seen = True
            elif c.name == "read_file_content" and c.arguments.get("fileId") == target:
                seen = True
            if seen:
                break
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


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
