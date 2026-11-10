def ready(world):
    db_row = next((d for d in world.state()["databases"] if d["database_id"] == "db_restricted_finance"), None)
    if not db_row:
        return "db_restricted_finance not found"
    if db_row["allowed_access"] != 0:
        return "expected db_restricted_finance to have allowed_access = 0"
    return None