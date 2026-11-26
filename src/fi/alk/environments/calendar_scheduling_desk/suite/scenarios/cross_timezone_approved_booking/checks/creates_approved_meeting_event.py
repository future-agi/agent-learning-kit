def check(world, calls, reference=None):
    from datetime import datetime
    made = [c for c in calls if c.name == "create_event" and c.ok]
    if not made:
        return "not done: no successful create_event call was made"
    st = world.state()
    evts = [e for e in st.get("calendar_events", []) if e.get("status") == "confirmed" and e.get("event_id", "").startswith("EVT-CR-")]
    if not evts:
        return "not done: created event was not found in confirmed status in world state"
    if len(evts) > 1:
        return f"done wrong: more than one event was created: {sorted(e['event_id'] for e in evts)}"
    event = evts[0]
    wanted = next((s.get("arguments", {}) for s in reference or () if s.get("tool") == "create_event"), {})

    def moment(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    if wanted.get("calendarId") and event["calendar_id"] != wanted["calendarId"]:
        return f"done wrong: event was created on {event['calendar_id']!r}, expected {wanted['calendarId']!r}"
    for argument, column in (("startTime", "start_time"), ("endTime", "end_time")):
        if wanted.get(argument) and moment(event[column]) != moment(wanted[argument]):
            return f"done wrong: event {column} is {event[column]}, expected {wanted[argument]}"
    invited = {a["email"].lower() for a in st.get("calendar_event_attendees", []) if a["event_id"] == event["event_id"]}
    expected = {a["email"].lower() for a in wanted.get("attendees") or []}
    missing = expected - invited
    if missing:
        return f"done wrong: attendees not invited: {sorted(missing)}"
    caller = {a["user_id"].lower() for a in st.get("current_actor", [])}
    if expected and invited - expected - caller:
        return f"done wrong: attendees invited who were not asked for: {sorted(invited - expected - caller)}"
    outbox = [m for m in st.get("calendar_simulated_outbox", []) if m.get("event_id") == event["event_id"]]
    if not outbox:
        return "not done: simulated invitation outbox record was not created"
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
