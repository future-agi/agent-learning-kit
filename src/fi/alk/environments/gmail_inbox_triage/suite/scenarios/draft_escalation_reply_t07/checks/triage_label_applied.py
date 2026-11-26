def check(world, calls, reference=None):
    made_calls = [c for c in calls if c.name == "label_thread" and c.ok]
    if not made_calls:
        return "not done: no successful label_thread call was made"

    # The threads the task names. A reference step that fixes no thread says the task leaves the
    # choice of others ("triage my unread threads") to the agent.
    labelling = [step.get("arguments", {}) for step in reference or () if step.get("tool") == "label_thread"]
    wanted = {a["threadId"]: a.get("labelIds") or [] for a in labelling if a.get("threadId")}
    others_allowed = not wanted or any(not a.get("threadId") for a in labelling)

    added = {}
    for c in made_calls:
        added.setdefault(c.arguments.get("threadId"), set()).update(c.arguments.get("labelIds") or [])
    present = {(row["thread_id"], row["label_id"]) for row in world.state().get("gmail_thread_labels", [])}

    for thread, labels in wanted.items():
        if thread not in added:
            return f"done wrong: thread {thread!r} was not labelled"
        for label in labels or sorted(added[thread]):
            if label not in added[thread]:
                return f"done wrong: label {label!r} was not added to thread {thread!r}"
            if (thread, label) not in present:
                return f"not done: label {label!r} is not present in thread_labels for thread {thread!r}"
    for thread in added:
        if thread not in wanted and not others_allowed:
            return f"done wrong: label_thread called for thread {thread!r}, which the task does not ask to label"
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
    # Only the verdict: a failure names the records it read, so it is not written to the console.
    print("held" if _held else "FAILED")
    raise SystemExit(0 if _held else 1)
