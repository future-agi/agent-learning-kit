def check(world, calls):
    mutation_tools = {"create_event", "update_event", "delete_event"}
    mutation_calls = [c for c in calls if c.name in mutation_tools and c.ok]
    if not mutation_calls:
        return "not done: no successful mutation call was executed"
    
    for c in mutation_calls:
        args = c.arguments or {}
        app_id = args.get("approval_id")
        if not app_id:
            return f"done wrong: {c.name} was called without an approval_id"
            
    st = world.state()
    evts = st.get("events", [])
    outbox = st.get("simulated_outbox", [])
    
    for c in mutation_calls:
        if c.name == "create_event":
            invites = [m for m in outbox if m.get("action") == "invitation"]
            created = [e for e in evts if e.get("status") == "confirmed" and str(e.get("event_id", "")).startswith("EVT-CR-")]
            if not invites or not created:
                return "not done: create_event call did not create new confirmed event and outbox invitations"
        elif c.name == "update_event":
            updates = [m for m in outbox if m.get("action") == "update"]
            updated = [e for e in evts if e.get("version", 1) > 1 and e.get("status") == "confirmed"]
            if not updates or not updated:
                return "not done: update_event call did not update event version and outbox notification"
        elif c.name == "delete_event":
            cancels = [m for m in outbox if m.get("action") == "cancellation"]
            cancelled = [e for e in evts if e.get("status") == "cancelled"]
            if not cancels or not cancelled:
                return "not done: delete_event call did not mark event cancelled and create cancellation notice"
                
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
