def check(world, calls, reference=None):
    from datetime import datetime
    made = [c for c in calls if c.name == "update_event" and c.ok]
    if not made:
        return "not done: no successful update_event call was made"
    st = world.state()
    evts = [e for e in st.get("calendar_events", []) if e.get("version", 1) > 1 and e.get("status") == "confirmed"]
    if not evts:
        return "not done: updated event version was not incremented in world state"
    wanted = next((s.get("arguments", {}) for s in reference or () if s.get("tool") == "update_event"), {})
    target = next((e for e in evts if e["event_id"] == wanted.get("eventId", e["event_id"])), None)
    if target is None:
        return f"done wrong: event {wanted['eventId']} was not updated"
    for argument, column in (("startTime", "start_time"), ("endTime", "end_time")):
        if wanted.get(argument) and datetime.fromisoformat(target[column].replace("Z", "+00:00")) != datetime.fromisoformat(wanted[argument].replace("Z", "+00:00")):
            return f"done wrong: event {column} is {target[column]}, expected {wanted[argument]}"
    outbox = [m for m in st.get("calendar_simulated_outbox", []) if m.get("action") == "update"]
    if not outbox:
        return "not done: reschedule update notification was not found in outbox"
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
