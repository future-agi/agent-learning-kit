def check(world, calls, reference=None):
    import re
    state = world.state()
    messages = state.get("gmail_messages", [])
    thread_of = {m["id"]: m["thread_id"] for m in messages}
    wanted_thread = None
    for step in reference or ():
        if step.get("tool") == "create_draft":
            wanted_thread = thread_of.get(step.get("arguments", {}).get("replyToMessageId")) or wanted_thread
    if not any(c.name == "create_draft" and c.ok for c in calls):
        return "not done: no successful create_draft call was made"
    drafts = [d for d in state.get("gmail_drafts", []) if not wanted_thread or d.get("thread_id") == wanted_thread]
    if not drafts:
        return f"not done: no draft on thread {wanted_thread!r}"
    thread = wanted_thread or drafts[-1]["thread_id"]
    if not any(c.name == "get_thread" and c.ok and c.arguments.get("threadId") == thread for c in calls):
        return f"not done: thread {thread!r} was not read with get_thread, so the correction could not have been seen"
    # The customer is whoever wrote first; their later message governs.
    in_thread = sorted((m for m in messages if m["thread_id"] == thread), key=lambda m: m["date"])
    customer = in_thread[0]["sender"].lower()
    theirs = [m for m in in_thread if m["sender"].lower() == customer]
    if len(theirs) < 2:
        return None
    days = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
    latest = {d.lower() for d in days.findall(theirs[-1]["body"])}
    earlier = {d.lower() for m in theirs[:-1] for d in days.findall(m["body"])}
    superseded = earlier - latest
    for d in drafts:
        said = {x.lower() for x in days.findall(d.get("body") or "")}
        if said & superseded:
            return f"done wrong: the draft confirms {', '.join(sorted(said & superseded))}, which the customer withdrew in a later message"
        if said and latest and not said & latest:
            return f"done wrong: the draft names {', '.join(sorted(said))} but the customer's latest message asks for {', '.join(sorted(latest))}"
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
