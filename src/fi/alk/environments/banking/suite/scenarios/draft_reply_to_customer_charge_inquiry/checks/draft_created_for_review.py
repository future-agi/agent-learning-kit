def check(world, calls, reference=None):
    wanted_message = wanted_thread = None
    for step in reference or ():
        if step.get("tool") == "create_draft":
            wanted_message = step.get("arguments", {}).get("replyToMessageId") or wanted_message
            wanted_thread = step.get("arguments", {}).get("thread_id") or wanted_thread
    state = world.state()
    if wanted_message and not wanted_thread:
        wanted_thread = next((m.get("thread_id") for m in state.get("messages", []) if m.get("id") == wanted_message), None)
    draft_calls = [c for c in calls if c.name == "create_draft" and c.ok]
    if not draft_calls:
        return "not done: create_draft was not called"
    drafts = state.get("drafts", [])
    if not drafts:
        return "not done: no draft found in database"
    if wanted_thread and not any(d.get("thread_id") == wanted_thread for d in drafts):
        return f"done wrong: no draft on the customer's thread {wanted_thread!r}"
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
