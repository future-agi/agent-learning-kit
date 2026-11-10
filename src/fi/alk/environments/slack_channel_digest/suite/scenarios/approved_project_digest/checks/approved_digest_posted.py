def check(world, calls, reference=None):
    st = world.state()
    ledger = st.get("message_ledger", [])
    if not ledger:
        return "not done: no record in message_ledger"
    delivered = [r for r in ledger if r.get("status") == "delivered"]
    if not delivered:
        return "not done: no delivered record in message_ledger"
        
    if reference:
        for step in reference:
            if step.get("tool") == "slack_post_message":
                wanted_key = step.get("arguments", {}).get("request_key")
                if wanted_key and not any(r.get("request_key") == wanted_key for r in delivered):
                    return f"done wrong: expected request_key in ledger to be {wanted_key!r}"
                    
    msgs = st.get("messages", [])
    delivered_ids = {r.get("message_id") for r in delivered if r.get("message_id")}
    matching = [m for m in msgs if m.get("message_id") in delivered_ids]
    if not matching:
        return "not done: message not recorded in messages table"
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
