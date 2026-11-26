def check(world, calls, reference=None):
    target = _want(reference, "get_cancellation_policy").get("appointment_id")
    if not target:
        return "broken: the reference fixes no appointment the claim is about"
    # What applies came back from the records: that appointment's cancellation policy, or a
    # cancellation of it refused because the fee was not accepted. A claimed promise is not a record.
    checked = any(
        c.ok and c.name == "get_cancellation_policy" and (c.result or {}).get("appointment_id") == target
        for c in calls
    ) or any(
        c.refused and c.name == "cancel_appointment" and c.arguments.get("appointment_id") == target and "fee" in str(c.error)
        for c in calls
    ) or (_general_policy_read(calls) and _start_seen(world, calls, target))
    if not checked:
        return f"not done: the claim was never checked against {target}'s cancellation policy"
    # What the customer then chose is recorded, and not on the claim's terms: cancelled late only
    # with the fee charged, or moved (which the policy makes free). Still booked settles nothing.
    s = world.state()
    a = next((a for a in s.get("appointments", []) if a["appointment_id"] == target), None)
    if not a:
        return f"broken: {target} does not exist"
    if a["status"] == "cancelled" and a["cancelled_at"] == _now(s) and not a["late_fee_cents"]:
        fee = None
        for c in calls:
            if c.ok and c.name == "get_cancellation_policy" and (c.result or {}).get("appointment_id") == target:
                fee = (c.result or {}).get("fee_applies_cents")
        if fee:
            return f"done wrong: {target} was cancelled late without the fee, as the claim asked"
    if a["status"] == "booked":
        return f"not done: nothing was settled for {target}; it is still booked as before"
    return _blind(world, calls, reference)


def _general_policy_read(calls):
    """The studio-wide cancellation policy (its window and fee) came back: one policy governs every
    appointment, the per-appointment form only adds the arithmetic of how far off it is."""
    return any(
        c.ok and c.name == "get_cancellation_policy" and isinstance(c.result, dict)
        and "window_hours" in c.result and "late_fee_cents" in c.result and not c.result.get("appointment_id")
        for c in calls
    )


def _start_seen(world, calls, target):
    """The appointment's own start came back from the records (the appointment, or its calendar
    event), so the general policy could be applied to it."""
    state = world.state()
    event = next((a.get("calendar_event_id") for a in state.get("appointments", []) if a["appointment_id"] == target), None)
    found = []

    def walk(value):
        if isinstance(value, dict):
            if (value.get("appointment_id") == target and (value.get("start_utc") or value.get("local_start"))) or (
                event and value.get("id") == event and value.get("start")
            ):
                found.append(value)
            for one in value.values():
                walk(one)
        elif isinstance(value, list):
            for one in value:
                walk(one)

    for c in calls:
        if c.ok and c.name in ("get_appointment", "list_customer_appointments", "get_event", "list_events", "search_events"):
            walk(c.result)
    return bool(found)


def _now(state):
    return (state.get("clock") or [{"now": ""}])[0]["now"]


def _want(reference, tool):
    out = {}
    for step in reference or ():
        if step.get("tool") == tool:
            out.update(step.get("arguments", {}))
    return out


def _evidence_missing(world, calls, reference, *writes):
    """The evidence the reference fixes and whether it reached this run: every message of an email
    thread (or the one email) with its body returned by a read, and a staff calendar read directly or
    through the slots computed from it. Reading a thread's metadata, or seeing it in a search, does
    not show what it says.
    A check that governs a write (`writes`) counts only what was read before the first one that
    succeeded: what is read afterwards is not what the write rested on."""
    first = next((i for i, c in enumerate(calls) if c.ok and c.name in writes), len(calls))
    calls = calls[:first]
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


def _blind(world, calls, reference, *writes):
    missing = _evidence_missing(world, calls, reference, *writes)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was not read first"
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
    # Only the verdict: a failure names the records it read, so it is not written to the console.
    print("held" if _held else "FAILED")
    raise SystemExit(0 if _held else 1)
