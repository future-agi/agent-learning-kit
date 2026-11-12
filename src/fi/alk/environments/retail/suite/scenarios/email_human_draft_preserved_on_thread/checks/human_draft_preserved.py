def check(world, calls, reference=None):
    import json
    state = world.state()
    drafts = state.get("gmail_drafts", [])
    human = [d for d in drafts if d.get("is_human_edited")]
    if not human:
        return "not done: no human-edited draft exists to preserve"
    for hd in human:
        if len([d for d in drafts if d["thread_id"] == hd["thread_id"]]) > 1:
            return f"done wrong: another draft was created on thread {hd['thread_id']!r}"
    # The person's draft was found: a listing that returned it, or the store refusing a second
    # draft on its thread because it is there.
    found = any(
        c.ok and c.name == "list_drafts" and any(hd["id"] in json.dumps(c.result, default=str) for hd in human)
        for c in calls
    ) or any(
        c.name == "create_draft" and not c.ok and str(c.error or "").startswith("conflict")
        for c in calls
    )
    if not found:
        return "not done: the person's draft on the thread was never found"
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
