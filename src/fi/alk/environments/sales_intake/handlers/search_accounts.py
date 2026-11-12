def handle(args, db):
    name, domain = args.get("name"), args.get("domain")
    if not (name or domain):
        raise ToolError("Give a name or a domain.", category="invalid_input")
    out = []
    for a in db.query("SELECT * FROM accounts"):
        if name and name.strip().lower() not in a["name"].lower():
            continue
        if domain and domain.strip().lower().lstrip("@") != a["domain"]:
            continue
        out.append({"account_id": a["account_id"], "name": a["name"], "domain": a["domain"], "country": a["country"]})
    return {"accounts": out, "count": len(out)}


