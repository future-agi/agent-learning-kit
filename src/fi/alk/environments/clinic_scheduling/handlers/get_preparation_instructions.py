

def _need(args, *names):
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}", category="invalid_input")


def handle(args, db):
    _need(args, "specialty")
    r = db.one("SELECT * FROM preparation_instructions WHERE specialty = ?", [args["specialty"]])
    if r is None:
        raise ToolError(f"No preparation instructions for {args['specialty']!r}.", category="not_found")
    forms = [f["form_id"] for f in db.query("SELECT form_id FROM intake_forms WHERE specialty IN ('all', ?) ORDER BY form_id", [args["specialty"]])]
    return {"specialty": r["specialty"], "instructions": r["instructions"], "intake_forms": forms}
