def handle(args, db):
    database_id = args.get("database_id")
    if not database_id:
        raise ToolError("Missing required parameter 'database_id'.")
    db_row = db.one("SELECT * FROM databases WHERE database_id = ?", (database_id,))
    if db_row is None:
        raise ToolError(f"Database '{database_id}' not found.")
    if not db_row["allowed_access"]:
        raise ToolError(f"Access denied to database '{database_id}'.")
    raw_schema = db_row["schema_json"]
    if isinstance(raw_schema, str):
        schema = json.loads(raw_schema)
    else:
        schema = raw_schema
    return {
        "database_id": db_row["database_id"],
        "schema": schema,
        "title": db_row["title"]
    }