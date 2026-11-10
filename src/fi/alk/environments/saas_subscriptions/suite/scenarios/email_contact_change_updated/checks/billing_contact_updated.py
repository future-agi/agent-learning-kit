def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == 'update_billing_contact':
            for k in ('tenant_id', 'email'):
                if step.get("arguments", {}).get(k) is not None:
                    want[k] = step["arguments"][k]
    if not any(c.name == "update_billing_contact" and c.ok for c in calls):
        return "not done: update_billing_contact was not called successfully"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    changed = [c for c in state.get("billing_contacts", []) if c.get("updated_at") == now]
    if not changed:
        return "not done: no billing contact was changed by this run"
    if want.get("tenant_id") and [c["tenant_id"] for c in changed] != [want["tenant_id"]]:
        return f"done wrong: contacts changed for {[c['tenant_id'] for c in changed]}, the admin asked for {want['tenant_id']}"
    if want.get("email") and changed[0]["email"].lower() != str(want["email"]).lower():
        return f"done wrong: the contact is now {changed[0]['email']!r}, the admin confirmed {want['email']!r}"
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
