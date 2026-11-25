def check(world, calls, reference=None):
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    want = {}
    for step in reference or ():
        if step.get("tool") == 'create_handoff':
            for k in ('lead_id', 'kind'):
                if step.get("arguments", {}).get(k) is not None:
                    want[k] = step["arguments"][k]
    if not any(c.name == "create_handoff" and c.ok for c in calls):
        return "not done: create_handoff was not called successfully"
    new = [h for h in state.get("handoffs", []) if h["created_at"] == now]
    if len(new) != 1:
        return f"done wrong: {len(new)} handoffs were opened, one was asked for"
    h = new[0]
    if want.get("lead_id") and h["lead_id"] != want["lead_id"]:
        return f"done wrong: the handoff is for {h['lead_id']}, not {want['lead_id']}"
    if want.get("kind") and h["kind"] != want["kind"]:
        return f"done wrong: a {h['kind']} handoff was opened, a {want['kind']} one was needed"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The email thread or Slack thread the reference fixes and whether its content reached this run:
    every message of the email thread (or the one email) with its body returned by a read, and the
    text of every message in the Slack thread (the desk's own posts aside) returned by a thread read,
    channel read or search. Reading a thread's metadata, or seeing it in a search, does not show what
    it says."""
    state = world.state()
    desk = (state.get("current_actor") or [{}])[0].get("user_id")
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("email thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("email", a["messageId"]))
        elif step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("Slack thread", a["message_ts"]))
    bodies = _bodies_shown(calls)
    texts = _texts_shown(calls)
    missing = []
    for kind, target in wanted:
        if kind in ("email thread", "email"):
            ids = [m["id"] for m in state.get("gmail_messages", []) if m["thread_id" if kind == "email thread" else "id"] == target]
            seen = bool(ids) and all(i in bodies for i in ids)
        elif kind == "Slack thread":
            needed = [m["ts"] for m in state.get("slack_messages", []) if (m["ts"] == target or m.get("thread_ts") == target) and m["user_id"] != desk]
            seen = bool(needed) and all(t in texts for t in needed)
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


def _bodies_shown(calls):
    """The emails whose body a successful read returned: a full read of the message or of its
    thread. A metadata-only or minimal read, or a search snippet, returns no body."""
    shown = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("id") and "plaintextBody" in value:
                shown.add(value["id"])
            for one in value.values():
                walk(one)
        elif isinstance(value, list):
            for one in value:
                walk(one)

    for c in calls:
        if c.ok and c.name in ("get_thread", "get_message"):
            walk(c.result)
    return shown


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
