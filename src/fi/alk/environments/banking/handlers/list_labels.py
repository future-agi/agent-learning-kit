def handle(args, db):
    labels = db.query("SELECT * FROM labels ORDER BY name ASC")
    return {"labels": labels}
