def handle(args, db):
    drafts = db.query("SELECT * FROM drafts ORDER BY updated_at DESC")
    return {"drafts": drafts}
