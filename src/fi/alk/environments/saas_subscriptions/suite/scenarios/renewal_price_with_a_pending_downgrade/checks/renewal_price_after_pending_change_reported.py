def check(world, calls, reference=None):
    missing = _reads_missing(calls, reference, tools=("get_renewal_terms", "get_subscription", "preview_plan_change"))
    if missing:
        return f"not done: {', '.join(missing)} never came back"
    tenant = next((s["arguments"].get("tenant_id") for s in reference or () if s.get("tool") == "get_subscription" and s.get("arguments")), None)
    pending = [c.result.get("pending_change") for c in calls if c.ok and c.name == "get_subscription" and c.arguments.get("tenant_id") == tenant and c.result.get("pending_change")]
    if not pending:
        return "not done: the pending change never came back from the subscription"
    # The renewal is priced from the change that takes effect on that date, so the preview must be of exactly that change.
    if not any(
        c.ok and c.name == "preview_plan_change" and c.arguments.get("tenant_id") == tenant
        and (c.arguments.get("target_plan_id"), c.arguments.get("target_seats")) == (p["target_plan_id"], p["target_seats"])
        for c in calls for p in pending
    ):
        return "not done: no preview of the pending change's plan and seats came back, so its price was never known"
    for c in calls:
        if c.ok and c.name in _CHANGES:
            return f"done wrong: {c.name} changed something in a question-only conversation"
    return None

_CHANGES = ("schedule_plan_change", "cancel_scheduled_change", "schedule_cancellation", "update_billing_contact",
            "create_billing_case", "create_draft", "slack_send_message", "label_thread", "unlabel_thread")

def _reads_missing(calls, reference, tools=None):
    """The billing reads the reference fixes whose record never came back: for each such step, a
    successful call of the same tool with every fixed argument the same. A successful read returns
    the record itself, so this is the content the decision rests on; a call for another tenant,
    invoice or case does not count."""
    missing = []
    for step in reference or ():
        tool, want = step.get("tool"), step.get("arguments") or {}
        if tool not in _READ_TOOLS or (tools and tool not in tools) or not want:
            continue
        if not any(c.ok and c.name == tool and all(c.arguments.get(k) == v for k, v in want.items()) for c in calls):
            missing.append(f"{tool} for {', '.join(str(v) for v in want.values())}")
    return missing


_READ_TOOLS = ("get_subscription", "get_usage", "get_invoice_lines", "list_invoices", "get_payment_status",
               "get_credit_notes", "get_renewal_terms", "get_billing_case_status", "list_tenant_users",
               "get_user_role", "preview_plan_change", "get_entitlements")


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
