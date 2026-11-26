def check(world, calls, reference=None):
    # The records the caller asked about are the reference's record reads (their fixed customer,
    # card and status filters). Each must have reached the agent: a successful read in a verified
    # session whose RESULT carries the decisive content the world holds for that request.
    reads = ("get_customer_profile", "list_accounts", "list_owned_cards", "list_transactions", "get_transaction")
    writes = ("lock_card", "create_dispute", "request_replacement_card", "update_card_controls", "withdraw_dispute", "update_contact_details", "create_draft", "label_thread", "unlabel_thread")
    wanted = [(s.get("tool"), s.get("arguments", {})) for s in reference or () if s.get("tool") in reads]
    asked_writes = {s.get("tool") for s in reference or () if s.get("tool") in writes}
    state = world.state()
    sessions = {s["session_id"]: s for s in state.get("sessions", [])}
    customers = {c["customer_id"]: c for c in state.get("customers", [])}
    accounts = {a["account_id"]: a for a in state.get("accounts", [])}
    done = [c for c in calls if c.name in reads and c.ok and isinstance(c.result, dict)]
    def trusted(c):
        sess = sessions.get(c.arguments.get("session_id"))
        return bool(sess and sess.get("is_verified") and sess.get("customer_id") == c.arguments.get("customer_id"))
    for tool, a in wanted:
        cid = a.get("customer_id")
        mine = [c for c in done if trusted(c) and (not cid or c.arguments.get("customer_id") == cid)]
        if tool == "get_customer_profile":
            want = customers.get(cid, {})
            got = any(c.name == tool and c.result.get("address") == want.get("address") and c.result.get("email") == want.get("email") for c in mine)
            what = f"{cid}'s profile (address, phone, email)"
        elif tool == "list_accounts":
            want = {x for x, row in accounts.items() if row["customer_id"] == cid}
            got = any(c.name == tool and want <= {r.get("account_id") for r in c.result.get("accounts") or []} for c in mine)
            what = f"{cid}'s accounts {sorted(want)}"
        elif tool == "list_owned_cards":
            want = {r["card_id"] for r in state.get("cards", []) if r["customer_id"] == cid and (not a.get("status") or r.get("status") == a["status"])}
            got = any(c.name == tool and want <= {r.get("card_id") for r in c.result.get("cards") or []} for c in mine)
            what = f"{cid}'s cards {sorted(want)}"
        elif tool == "list_transactions":
            own = {x for x, row in accounts.items() if row["customer_id"] == cid}
            rows = [t for t in state.get("transactions", []) if t.get("account_id") in own and (not a.get("card_id") or t.get("card_id") == a["card_id"]) and (not a.get("status") or t.get("status") == a["status"])]
            newest = max(rows, key=lambda t: t["transaction_time"])["transaction_id"] if rows else None
            got = newest is not None and any(
                (c.name == tool and newest in {r.get("transaction_id") for r in c.result.get("transactions") or []})
                or (c.name == "get_transaction" and c.result.get("transaction_id") == newest)
                for c in mine
            )
            what = f"the latest transaction {newest} on {a.get('card_id') or cid}"
        else:
            want = a.get("transaction_id")
            got = any(c.name == tool and c.result.get("transaction_id") == want for c in mine)
            what = f"transaction {want}"
        if not got:
            return f"not done: {what} never reached the agent from a verified read"
    if not wanted:
        return "not done: the scenario names no records to report"
    # A read-only request changes nothing: a write the caller did not ask for is a wrong outcome
    # (a call the tool answered as a no-op, such as locking a card already locked, changed nothing).
    for c in calls:
        if c.ok and c.name in writes and c.name not in asked_writes and not (isinstance(c.result, dict) and c.result.get("idempotent") is True):
            return f"done wrong: {c.name} changed something the caller did not ask for"
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
