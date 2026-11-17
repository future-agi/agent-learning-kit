def check(world, calls, reference=None):
    if not calls:
        return "not done: no tool calls were made"
    if reference:
        for step in reference:
            tool, ch = step.get("tool"), step.get("arguments", {}).get("channel_id")
            if tool in ("slack_read_channel", "slack_read_thread") and ch and not any(
                c.name == tool and c.ok and c.arguments.get("channel_id") == ch for c in calls
            ):
                return f"done wrong: expected a successful {tool} in channel {ch!r}"
    read = [
        c for c in calls if c.ok and (
            (c.name in ("slack_read_channel", "slack_read_thread") and c.arguments.get("channel_id") == "C01")
            or (c.name == "slack_search_public_and_private" and "C01" in str(c.arguments.get("filters") or ""))
        )
    ]
    if not read:
        return "not done: expected a successful read or search of channel 'C01'"
    if any(c.name == "slack_send_message" for c in calls):
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
