def ready(world):
    s = world.state()
    accounts = {a["account_id"]: a for a in s.get("accounts", [])}
    if "ACC02" not in accounts:
        return "ACC02 account missing"
    if accounts["ACC02"].get("employee_count") is not None:
        return "ACC02 has employee_count populated"
    return None
