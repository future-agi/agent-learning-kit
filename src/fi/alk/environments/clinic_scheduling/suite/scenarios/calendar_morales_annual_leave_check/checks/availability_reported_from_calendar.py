def check(world, calls, reference=None):
    """The availability the answer rests on reached the agent: each provider calendar the reference
    reads was returned (or the slots computed from it), and each slot query it fixes (a provider or
    specialty, a location, a date range) returned the open slots for at least that range."""
    queries, calendars = [], False
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "find_available_slots" and (a.get("provider_id") or a.get("specialty")):
            queries.append(a)
        if (step.get("tool") == "list_events" and a.get("calendarId")) or (step.get("tool") == "get_event" and a.get("eventId")):
            calendars = True
    if not queries and not calendars:
        return "not done: the reference fixes no calendar or slot query"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    for q in queries:
        answered = any(
            c.name == "find_available_slots" and c.ok and isinstance(c.result, dict) and isinstance(c.result.get("slots"), list)
            and all(c.arguments.get(k) == q[k] for k in ("provider_id", "specialty", "location_id") if q.get(k))
            and (not q.get("date_from") or str(c.arguments.get("date_from") or "9999") <= q["date_from"])
            and (not q.get("date_to") or str(c.arguments.get("date_to") or "") >= q["date_to"])
            for c in calls
        )
        if not answered:
            what = ", ".join(f"{k}={v}" for k, v in q.items() if k in ("provider_id", "specialty", "location_id", "date_from", "date_to"))
            return f"done blind: the open slots for {what} were never returned"
    return None

def _evidence_missing(world, calls, reference):
    """The evidence the reference fixes and whether it reached this run: every message of an email
    thread (or the one email) with its body returned by a read, and a provider calendar read directly
    or through the slots computed from it. Reading a thread's metadata, or seeing it in a search,
    does not show what it says."""
    state = world.state()
    cal_of = {e["event_id"]: e["calendar_id"] for e in state.get("calendar_events", [])}
    prov_of_cal = {p["calendar_id"]: p["provider_id"] for p in state.get("providers", [])}
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
            wanted.append(("calendar", cal_of.get(a["eventId"], a.get("calendarId"))))
    bodies = _bodies_shown(calls)
    missing = []
    for kind, target in wanted:
        if kind in ("email thread", "email"):
            ids = [m["id"] for m in state.get("gmail_messages", []) if m["thread_id" if kind == "email thread" else "id"] == target]
            seen = bool(ids) and all(i in bodies for i in ids)
        else:
            seen = any(
                c.ok and (
                    (c.name == "list_events" and c.arguments.get("calendarId") == target)
                    or (c.name == "get_event" and cal_of.get(c.arguments.get("eventId")) == target)
                    or (c.name == "find_available_slots" and (
                        c.arguments.get("provider_id") == prov_of_cal.get(target)
                        or any(isinstance(s, dict) and s.get("provider_id") == prov_of_cal.get(target) for s in (c.result or {}).get("slots", []))
                    ))
                )
                for c in calls
            )
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
