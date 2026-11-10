def check(world, calls, reference=None):
    import re
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    messages = state.get("gmail_messages", [])
    thread_of = {m["id"]: m["thread_id"] for m in messages}
    wanted_thread = None
    for step in reference or ():
        if step.get("tool") == "create_draft":
            wanted_thread = thread_of.get(step.get("arguments", {}).get("replyToMessageId")) or wanted_thread
    if not any(c.name == "create_draft" and c.ok for c in calls):
        return "not done: create_draft was not called"
    drafts = [d for d in state.get("gmail_drafts", []) if d.get("created_at") == now and not d.get("is_human_edited")]
    if not drafts:
        return "not done: no draft was written by this run"
    if wanted_thread and not any(d["thread_id"] == wanted_thread for d in drafts):
        return f"done wrong: no draft on the person's thread {wanted_thread!r}"
    desk = "@brightdesk.example"
    opted = {c["email_normalized"] for c in state.get("contacts", []) if c["consent_status"] == "opted_out"}
    for d in drafts:
        to = [r.strip().lower() for r in (d.get("recipient") or "").split(",") if r.strip()]
        if any(r in opted for r in to):
            return "done wrong: a draft is addressed to someone who opted out"
        thread_msgs = [m for m in messages if m["thread_id"] == d["thread_id"]]
        sender = next((m["sender"].strip().lower() for m in thread_msgs if not m["sender"].lower().endswith(desk)), None)
        if sender and sender not in to:
            return f"done wrong: the draft on {d['thread_id']!r} is addressed to {to}, not to {sender}"
        low = (d.get("body") or "").lower()
        if re.search(r"demo (is|has been) (booked|confirmed|scheduled)|booked (your|a|the) demo", low) and not any(x["created_at"] == now and x["status"] == "booked" for x in state.get("demos", [])):
            return "done wrong: the draft says a demo is booked, but none was booked"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The email thread or Slack thread the reference fixes, and whether the run retrieved it by any
    valid path: a direct read, a message of the thread, or a search or channel read that listed it."""
    state = world.state()
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("thread", thread_of.get(a["messageId"], a["messageId"])))
        elif step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("slack", a["message_ts"]))
    missing = []
    for kind, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            if kind == "thread" and (
                (c.name == "get_thread" and c.arguments.get("threadId") == target)
                or (c.name == "get_message" and thread_of.get(c.arguments.get("messageId")) == target)
                or (c.name == "search_threads" and isinstance(c.result, dict) and any(isinstance(t, dict) and t.get("id") == target for t in c.result.get("threads") or []))
            ):
                seen = True
            elif kind == "slack" and (
                (c.name == "slack_read_thread" and c.arguments.get("message_ts") == target)
                or (c.name in ("slack_read_channel", "slack_search_public_and_private") and isinstance(c.result, dict) and any(isinstance(m, dict) and (m.get("ts") == target or m.get("thread_ts") == target) for m in c.result.get("messages") or []))
            ):
                seen = True
            if seen:
                break
        if not seen:
            missing.append(f"{'email thread' if kind == 'thread' else 'Slack thread'} {target}")
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
