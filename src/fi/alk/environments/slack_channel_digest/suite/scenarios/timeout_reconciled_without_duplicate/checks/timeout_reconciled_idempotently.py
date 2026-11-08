def check(world, calls, reference=None):
    if not calls:
        return "not done: no tool calls were made"
    key = "REQ-TIMEOUT-501"
    if reference:
        for step in reference:
            if step.get("tool") == "slack_check_message_status":
                key = step.get("arguments", {}).get("request_key") or key
    status = [c for c in calls if c.name == "slack_check_message_status"]
    if status and not any(c.ok and c.arguments.get("request_key") == key for c in status):
        return f"done wrong: the status check was not a successful check of request_key {key!r}"
    if not status and not any(
        c.name == "slack_read_channel" and c.ok and c.arguments.get("channel_id") == "C01" for c in calls
    ):
        return "not done: expected a status check for the request key or a read-back of channel 'C01'"
    st = world.state()
    ledger = [r for r in st.get("slack_message_ledger", []) if r.get("request_key") == key]
    if len(ledger) > 1:
        return "failed: duplicate entries created in message_ledger"
    if any(c.name == "slack_send_message" and c.ok and c.arguments.get("request_key") != key for c in calls):
        return "failed: a new message was posted instead of reconciling the committed one"
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
