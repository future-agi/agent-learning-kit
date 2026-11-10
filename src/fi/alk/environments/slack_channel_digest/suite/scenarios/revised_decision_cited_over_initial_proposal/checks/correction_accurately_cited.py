def check(world, calls, reference=None):
    if not calls:
        return "not done: no tool calls were made"
    if reference:
        for step in reference:
            if step.get("tool") == "slack_search_messages":
                ch = step.get("arguments", {}).get("channel_id")
                if ch:
                    matching = [
                        c for c in calls
                        if c.name == "slack_search_messages" and c.ok and c.arguments.get("channel_id") == ch
                    ]
                    if not matching:
                        return f"done wrong: expected search in channel {ch!r}"
    thread_calls = [
        c for c in calls
        if c.name in ("slack_search_messages", "slack_get_thread") and c.ok and c.arguments.get("channel_id") == "C01"
    ]
    if not thread_calls:
        return "not done: expected successful message search or thread retrieval in channel 'C01'"
    if any(c.name == "slack_post_message" for c in calls):
        return "failed: agent unexpectedly posted a message during inquiry"
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
