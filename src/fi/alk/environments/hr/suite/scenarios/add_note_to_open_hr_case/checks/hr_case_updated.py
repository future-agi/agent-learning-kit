def check(world, calls, reference=None):
    cid = None
    for step in reference or ():
        if step.get("tool") == "update_hr_case" and step.get("arguments", {}).get("case_id"):
            cid = step["arguments"]["case_id"]
    made = [c for c in calls if c.name == "update_hr_case" and c.ok]
    if not made:
        return "not done: update_hr_case was not called successfully"
    state = world.state()
    cases = {c["case_id"]: c for c in state.get("hr_cases", [])}
    for c in made:
        got = c.arguments.get("case_id")
        if cid and got != cid:
            return f"done wrong: case {got} was updated, not {cid}"
        if not c.arguments.get("note") or str(c.arguments["note"]) not in (cases.get(got, {}).get("notes") or ""):
            return f"not done: the note is not on case {got}"
    # The open case is the place for the new information: a second case for the same employee and
    # category is the duplicate the update avoids.
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    target = cases.get(cid) if cid else None
    if target:
        dupes = [c["case_id"] for c in cases.values() if c.get("created_at") == now and c["employee_id"] == target["employee_id"] and c["category"] == target["category"]]
        if dupes:
            return f"done wrong: {', '.join(dupes)} duplicates the open case {cid}"
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
