def check(world, calls, reference=None):
    made = [c for c in calls if c.name == "label_thread" and c.ok]
    if not made:
        return "not done: no successful label_thread call was made"
    wanted_thread = wanted_labels = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "label_thread" and a.get("threadId"):
            wanted_thread = a["threadId"]
            wanted_labels = a.get("labelIds") or wanted_labels
    tl = world.state().get("gmail_thread_labels", [])
    for c in made:
        t_id = c.arguments.get("threadId")
        if wanted_thread and t_id != wanted_thread:
            return f"done wrong: label_thread called for thread {t_id!r}, expected {wanted_thread!r}"
        added = c.arguments.get("labelIds") or []
        for lid in (wanted_labels or []):
            if lid not in added:
                return f"done wrong: label {lid!r} was not added to {t_id!r}"
        for lid in added:
            if not any(r["thread_id"] == t_id and r["label_id"] == lid for r in tl):
                return f"not done: label {lid!r} is not on {t_id!r}"
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
