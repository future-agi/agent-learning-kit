def handle(args, db):
    q = "SELECT * FROM leads WHERE status NOT IN ('disqualified', 'closed')"
    p = []
    for k in ("contact_id", "account_id", "owner_rep_id"):
        if args.get(k):
            q += f" AND {k} = ?"
            p.append(args[k])
    rows = [_lead_view(l) for l in db.query(q + " ORDER BY lead_id", p)]
    return {"leads": rows, "count": len(rows)}


def _lead_view(l):
    return {k: l[k] for k in ("lead_id", "contact_id", "account_id", "source", "use_case", "seats", "timeline", "country", "budget", "status", "owner_rep_id", "queue_id", "created_at", "updated_at")}
