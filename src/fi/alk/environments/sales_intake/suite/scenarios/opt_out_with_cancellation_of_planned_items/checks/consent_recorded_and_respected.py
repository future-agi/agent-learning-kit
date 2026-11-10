def check(world, calls, reference=None):
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    want = {}
    for step in reference or ():
        if step.get("tool") == 'update_contact_consent':
            for k in ('contact_id', 'status'):
                if step.get("arguments", {}).get(k) is not None:
                    want[k] = step["arguments"][k]
    if not any(c.name == "update_contact_consent" and c.ok for c in calls):
        return "not done: update_contact_consent was not called successfully"
    contacts = {c["contact_id"]: c for c in state.get("contacts", [])}
    cid = want.get("contact_id")
    c = contacts.get(cid) if cid else None
    status = want.get("status") or "opted_out"
    if cid and (not c or c["consent_status"] != status):
        return f"not done: contact {cid} is not {status.replace('_', ' ')}"
    if cid and not any(e["contact_id"] == cid and e["created_at"] == now for e in state.get("consent_log", [])):
        return f"not done: no consent record for {cid} in this run"
    if c and status == "opted_out":
        planned = [o["item_id"] for o in state.get("outreach_queue", []) if o["contact_id"] == cid and o["status"] == "planned"]
        if planned:
            return f"done wrong: outreach {planned} is still planned for {cid}"
        email = c["email_normalized"]
        if any(email in (d.get("recipient") or "").lower() and d.get("created_at") == now for d in state.get("gmail_drafts", [])):
            return f"done wrong: a draft to {email} was written after the opt-out"
        leads = {l["lead_id"] for l in state.get("leads", []) if l["contact_id"] == cid}
        if any(h["lead_id"] in leads and h["kind"] == "follow_up" and h["created_at"] == now for h in state.get("handoffs", [])):
            return "done wrong: a follow-up was planned after the opt-out"
        if any(d["lead_id"] in leads and d["created_at"] == now for d in state.get("demos", [])):
            return "done wrong: a demo was booked after the opt-out"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The email thread or Slack thread the reference fixes, and whether the run retrieved it by any
    valid path: a direct read, a message of the thread, or a search or channel read that listed it."""
    state = world.state()
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("thread", thread_of.get(a["messageId"], a["messageId"])))
        elif step.get("tool") == "slack_read_thread" and a.get("message_ts"):
            wanted.append(("slack", a["message_ts"]))
    missing = []
    for kind, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            if kind == "thread" and (
                (c.name == "get_thread" and c.arguments.get("threadId") == target)
                or (c.name == "get_message" and thread_of.get(c.arguments.get("messageId")) == target)
                or (c.name == "search_threads" and isinstance(c.result, dict) and any(isinstance(t, dict) and t.get("id") == target for t in c.result.get("threads") or []))
            ):
                seen = True
            elif kind == "slack" and (
                (c.name == "slack_read_thread" and c.arguments.get("message_ts") == target)
                or (c.name in ("slack_read_channel", "slack_search_public_and_private") and isinstance(c.result, dict) and any(isinstance(m, dict) and (m.get("ts") == target or m.get("thread_ts") == target) for m in c.result.get("messages") or []))
            ):
                seen = True
            if seen:
                break
        if not seen:
            missing.append(f"{'email thread' if kind == 'thread' else 'Slack thread'} {target}")
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
    print("held" if _held else f"FAILED: {_said}")
    raise SystemExit(0 if _held else 1)
