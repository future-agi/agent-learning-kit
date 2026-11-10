def check(world, calls, reference=None):
    import re
    want_channel = want_thread = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "slack_send_message":
            want_channel = a.get("channel_id") or want_channel
            want_thread = a.get("thread_ts") or want_thread
    if not any(c.name == "slack_send_message" and c.ok for c in calls):
        return "not done: slack_send_message was not called successfully"
    state = world.state()
    actor = (state.get("current_actor") or [{}])[0].get("user_id")
    seed_ts = {m["ts"] for m in state.get("slack_messages", []) if m["user_id"] != actor}
    posted = [m for m in state.get("slack_messages", []) if m["user_id"] == actor and m["ts"] not in seed_ts]
    if not posted:
        return "not done: no reply by the desk is in the workspace"
    for m in posted:
        if want_channel and m["channel_id"] != want_channel:
            return f"done wrong: the reply went to {m['channel_id']}, not {want_channel}"
        if want_thread and m.get("thread_ts") != want_thread:
            return f"done wrong: the reply is not in the employee's thread {want_thread}"
        text = m["text"].lower()
        reqs = {r["request_id"]: r for r in state.get("leave_requests", [])}
        for rid in re.findall(r"\bREQ-\d{4}\b", m["text"]):
            r = reqs.get(rid)
            if r and r["status"] == "pending" and re.search(r"\b(is|has been|was) approved\b", text) and not re.search(r"not (yet )?approved|pending", text):
                return f"done wrong: the reply calls {rid} approved, but the HR system has it pending"
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
