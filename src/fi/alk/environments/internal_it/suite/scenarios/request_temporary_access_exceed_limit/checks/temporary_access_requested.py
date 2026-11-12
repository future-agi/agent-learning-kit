def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == "request_temporary_access":
            want = step.get("arguments", {})
    if not any(c.name == "request_temporary_access" and c.ok for c in calls):
        return "not done: request_temporary_access was not called successfully"
    now = (world.state().get("clock") or [{"now": ""}])[0]["now"]
    new = [r for r in world.state().get("access_requests", []) if r.get("created_at") == now and r.get("duration_days")]
    if not new:
        return "not done: no temporary request in the world"
    for k in ("employee_id", "app_id", "role_name", "duration_days"):
        if want.get(k) is not None and not any(r.get(k) == want[k] for r in new):
            return f"done wrong: no temporary request with {k} {want[k]}"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The Slack thread or library document the reference fixes and whether its content reached this
    run: the text of every message in the thread (the desk's own posts aside) returned by a thread
    read, channel read or search, and the document read."""
    state = world.state()
    desk = (state.get("current_actor") or [{}])[0].get("user_id")
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("Slack thread", a["message_ts"]))
        elif step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted.append(("file", a["fileId"]))
    texts = _texts_shown(calls)
    missing = []
    for kind, target in wanted:
        if kind == "Slack thread":
            needed = [m["ts"] for m in state.get("slack_messages", []) if (m["ts"] == target or m.get("thread_ts") == target) and m["user_id"] != desk]
            seen = bool(needed) and all(t in texts for t in needed)
        else:
            seen = any(c.ok and c.name == "read_file_content" and c.arguments.get("fileId") == target for c in calls)
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


def _texts_shown(calls):
    """The Slack messages whose text a successful thread read, channel read or search returned."""
    shown = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("ts") and "text" in value:
                shown.add(value["ts"])
            for one in value.values():
                walk(one)
        elif isinstance(value, list):
            for one in value:
                walk(one)

    for c in calls:
        if c.ok and c.name in ("slack_read_thread", "slack_read_channel", "slack_search_public_and_private"):
            walk(c.result)
    return shown


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
