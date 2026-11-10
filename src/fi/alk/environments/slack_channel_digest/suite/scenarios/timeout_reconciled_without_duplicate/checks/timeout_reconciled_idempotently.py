def check(world, calls, reference=None):
    if not calls:
        return "not done: no tool calls were made"
    if reference:
        for step in reference:
            if step.get("tool") == "slack_check_message_status":
                req = step.get("arguments", {}).get("request_key")
                if req and not any(c.name == "slack_check_message_status" and c.ok and c.arguments.get("request_key") == req for c in calls):
                    return f"done wrong: expected status check for request_key {req!r}"
    status_calls = [
        c for c in calls
        if c.name == "slack_check_message_status" and c.ok and c.arguments.get("request_key") == "REQ-TIMEOUT-501"
    ]
    if not status_calls:
        return "not done: expected successful status check for request_key 'REQ-TIMEOUT-501'"
    st = world.state()
    ledger = [r for r in st.get("message_ledger", []) if r.get("request_key") == "REQ-TIMEOUT-501"]
    if len(ledger) > 1:
        return "failed: duplicate entries created in message_ledger"
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
