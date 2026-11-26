def check(world, calls, reference=None):
    tenant = next((s["arguments"].get("tenant_id") for s in reference or () if s.get("tool") == "get_subscription" and s.get("arguments")), None)
    # Either record shows where the change stands: the subscription (what is pending now) or the
    # refusal of a cancel attempt, which names the change's status.
    shown = any(c.ok and c.name == "get_subscription" and c.arguments.get("tenant_id") == tenant for c in calls) or any(
        not c.ok and c.name == "cancel_scheduled_change" and c.arguments.get("tenant_id") == tenant and "status" in (c.error or "") for c in calls
    )
    if not shown:
        return f"not done: where {tenant}'s change stands never came back"
    for c in calls:
        if c.ok and c.name in _CHANGES:
            return f"done wrong: {c.name} succeeded; the change was already settled"
    return None

_CHANGES = ("schedule_plan_change", "cancel_scheduled_change", "schedule_cancellation", "update_billing_contact",
            "create_billing_case", "create_draft", "slack_send_message", "label_thread", "unlabel_thread")


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
