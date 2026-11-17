def check(world, calls, reference=None):
    if not any(c.ok for c in calls):
        return "not done: no successful mailbox call was made"
    if not any(c.name == "list_drafts" and c.ok for c in calls):
        return "not done: the existing drafts were never listed, so the human draft could not have been seen"
    state = world.state()
    drafts = state.get("gmail_drafts", [])
    human = [d for d in drafts if d.get("is_human_edited")]
    if not human:
        return "not done: no human-edited draft exists to preserve"
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    for hd in human:
        on_thread = [d for d in drafts if d["thread_id"] == hd["thread_id"]]
        if len(on_thread) > 1:
            return f"done wrong: another draft was created on thread {hd['thread_id']!r}, which already had a human-edited draft"
    for c in calls:
        if c.name == "create_draft" and c.ok and thread_of.get(c.arguments.get("replyToMessageId")) in {hd["thread_id"] for hd in human}:
            return "done wrong: an automated draft was created over an existing human draft"
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
