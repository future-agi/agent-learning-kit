def check(world, calls, reference=None):
    made_calls = [c for c in calls if c.name == "create_draft" and c.ok]
    if not made_calls:
        return "not done: no successful create_draft call was made"

    # The draft replies to a message; the thread it joins is that message's.
    thread_of = {m["id"]: m["thread_id"] for m in world.state().get("messages", [])}
    wanted_thread = None
    wanted_recipient = None
    if reference:
        for step in reference:
            if step.get("tool") == "create_draft":
                wanted_thread = thread_of.get(step.get("arguments", {}).get("replyToMessageId"))
                to = step.get("arguments", {}).get("to") or []
                wanted_recipient = to[0] if to else None

    # The reply is grounded in the thread: the agent must have read it (workbook M02, step 2).
    if wanted_thread and not any(
        c.name == "get_thread" and c.ok and c.arguments.get("threadId") == wanted_thread for c in calls
    ):
        return f"not done: thread {wanted_thread!r} was not read with get_thread before drafting"

    drafts = world.state().get("drafts", [])

    for c in made_calls:
        t_id = thread_of.get(c.arguments.get("replyToMessageId"))
        to = c.arguments.get("to") or []
        recip = ",".join(to)
        if wanted_thread and t_id != wanted_thread:
            return f"done wrong: draft created for thread {t_id!r}, expected {wanted_thread!r}"
        if wanted_recipient and wanted_recipient not in to:
            return f"done wrong: recipient expected {wanted_recipient!r}, got {to!r}"

        if not any(d["thread_id"] == t_id and d["recipient"] == recip for d in drafts):
            return f"not done: draft for thread {t_id!r} to {recip!r} is not present in world drafts table"

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
