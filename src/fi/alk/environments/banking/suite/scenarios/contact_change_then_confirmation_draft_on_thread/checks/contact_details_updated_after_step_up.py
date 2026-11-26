def check(world, calls, reference=None):
    wanted_field = wanted_value = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "update_contact_details":
            wanted_field = a.get("field") or wanted_field
            wanted_value = a.get("new_value") or wanted_value
    made = [c for c in calls if c.name == "update_contact_details" and c.ok]
    if not made:
        return "not done: update_contact_details was not called or did not succeed"
    state = world.state()
    sessions = {s["session_id"]: s for s in state.get("sessions", [])}
    customers = {c["customer_id"]: c for c in state.get("customers", [])}
    changes = state.get("contact_change_requests", [])
    for c in made:
        sess = sessions.get(c.arguments.get("session_id"))
        if not sess or not sess.get("is_verified") or not sess.get("step_up_at"):
            return f"done wrong: session {c.arguments.get('session_id')} changed contact details without step-up verification"
        field, value = c.arguments.get("field"), c.arguments.get("new_value")
        if wanted_field and field != wanted_field:
            return f"done wrong: {field} was changed, the customer asked to change their {wanted_field}"
        if wanted_value and value != wanted_value:
            return f"done wrong: {field} was set to {value!r}, the customer gave {wanted_value!r}"
        cust = customers.get(c.arguments.get("customer_id"))
        if not cust or cust.get(field) != value:
            return f"not done: the customer's {field} is not {value!r} in database"
        if not any(ch.get("customer_id") == c.arguments.get("customer_id") and ch.get("field") == field and ch.get("new_value") == value for ch in changes):
            return f"not done: no contact_change_requests row records the {field} change"
    missing = _evidence_missing(world, calls, reference, "update_contact_details")
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read before update_contact_details"
    return None

def _evidence_missing(world, calls, reference, write=None):
    """The evidence the reference says the decision rests on (the targets of its get_thread,
    get_message, read_file_content and lookup_merchant_descriptor steps, marked fixed), and which
    of them this run never retrieved by any valid path. Only content counts: a thread is read when
    a get_thread returned its message bodies or a get_message returned the body of its latest
    message (the one that governs); a search listing carries snippets only, so it locates a thread
    but does not read it. A file is read when read_file_content returned it (not an old revision).
    With `write`, only what was read before the first successful call of that tool counts: evidence
    read after the decision was acted on did not inform it."""
    if write:
        acted = next((i for i, c in enumerate(calls) if c.name == write and c.ok), len(calls))
        calls = calls[:acted]
    state = world.state()
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    latest = {}
    for m in sorted(state.get("gmail_messages", []), key=lambda m: m["date"]):
        latest[m["thread_id"]] = m["id"]
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("thread", thread_of.get(a["messageId"], a["messageId"])))
        elif step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted.append(("file", a["fileId"]))
        elif step.get("tool") == "lookup_merchant_descriptor" and (a.get("descriptor") or a.get("transaction_id")):
            wanted.append(("descriptor", a.get("descriptor") or a.get("transaction_id")))
    missing = []
    for kind, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            result = c.result if isinstance(c.result, dict) else {}
            if kind == "thread":
                if c.name == "get_thread" and c.arguments.get("threadId") == target and any(
                    isinstance(m, dict) and m.get("plaintextBody") for m in result.get("messages") or []
                ):
                    seen = True
                elif c.name == "get_message" and result.get("id") == latest.get(target) and result.get("plaintextBody"):
                    seen = True
            elif kind == "file" and c.name == "read_file_content" and c.arguments.get("fileId") == target and not c.arguments.get("revisionId"):
                seen = True
            elif kind == "descriptor" and c.name == "lookup_merchant_descriptor" and target in (
                c.arguments.get("descriptor"), c.arguments.get("transaction_id")
            ):
                seen = True
            if seen:
                break
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


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
