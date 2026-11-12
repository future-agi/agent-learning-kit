def check(world, calls, reference=None):
    s = world.state()
    cal_of = {r["staff_id"]: r["calendar_id"] for r in s.get("staff", [])}
    want_staff = _want(reference, "list_slots").get("staff_id")
    want_cal = _want(reference, "list_events").get("calendarId") or (cal_of.get(want_staff) if want_staff else None)
    looks = [c for c in calls if c.name in ("list_slots", "list_events", "get_event", "suggest_time") and c.ok]
    if not looks:
        return "not done: availability was never read from the calendar"
    if want_cal:
        ok = any((c.name == "list_events" and c.arguments.get("calendarId") == want_cal)
                 or (c.name == "list_slots" and (c.arguments.get("staff_id") == want_staff or any(cal_of.get(x.get("staff_id")) == want_cal for x in (c.result or {}).get("slots") or [])))
                 for c in looks)
        if not ok:
            return f"done wrong: {want_cal} was never read"
    return _blind(world, calls, reference)


def _now(state):
    return (state.get("clock") or [{"now": ""}])[0]["now"]


def _want(reference, tool):
    out = {}
    for step in reference or ():
        if step.get("tool") == tool:
            out.update(step.get("arguments", {}))
    return out


def _evidence_missing(world, calls, reference):
    """The evidence the reference fixes and whether it reached this run: every message of an email
    thread (or the one email) with its body returned by a read, and a staff calendar read directly or
    through the slots computed from it. Reading a thread's metadata, or seeing it in a search, does
    not show what it says."""
    state = world.state()
    cal_of_staff = {r["staff_id"]: r["calendar_id"] for r in state.get("staff", [])}
    cal_of_event = {e["event_id"]: e["calendar_id"] for e in state.get("calendar_events", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("email thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("email", a["messageId"]))
        elif step.get("tool") == "list_events" and a.get("calendarId"):
            wanted.append(("calendar", a["calendarId"]))
        elif step.get("tool") == "get_event" and a.get("eventId"):
            wanted.append(("calendar", cal_of_event.get(a["eventId"], a.get("calendarId"))))
    bodies = _bodies_shown(calls)
    missing = []
    for kind, target in wanted:
        if kind in ("email thread", "email"):
            ids = [m["id"] for m in state.get("gmail_messages", []) if m["thread_id" if kind == "email thread" else "id"] == target]
            seen = bool(ids) and all(i in bodies for i in ids)
        else:
            seen = False
            for c in calls:
                r = c.result if isinstance(c.result, dict) else {}
                if c.ok and (
                    (c.name == "list_events" and c.arguments.get("calendarId") == target)
                    or (c.name == "get_event" and cal_of_event.get(c.arguments.get("eventId")) == target)
                    or (c.name == "list_slots" and any(cal_of_staff.get(s.get("staff_id")) == target for s in r.get("slots") or []))
                    or (c.name == "list_slots" and c.arguments.get("staff_id") and cal_of_staff.get(c.arguments.get("staff_id")) == target)
                ):
                    seen = True
                    break
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


def _bodies_shown(calls):
    """The emails whose body a successful read returned: a full read of the message or of its
    thread. A metadata-only or minimal read, or a search snippet, returns no body."""
    shown = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("id") and "plaintextBody" in value:
                shown.add(value["id"])
            for one in value.values():
                walk(one)
        elif isinstance(value, list):
            for one in value:
                walk(one)

    for c in calls:
        if c.ok and c.name in ("get_thread", "get_message"):
            walk(c.result)
    return shown


def _blind(world, calls, reference):
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None


def _stray_events(state):
    now = _now(state)
    appts = {a["calendar_event_id"] for a in state.get("appointments", [])}
    cals = {r["calendar_id"] for r in state.get("staff", [])}
    return [e["event_id"] for e in state.get("calendar_events", []) if e["calendar_id"] in cals and e["created_at"] == now and e["event_id"] not in appts]


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
