def ready(world):
    s = world.state()
    accounts = {a["account_id"]: a for a in s.get("accounts", [])}
    if "ACC01" not in accounts:
        return "ACC01 account missing"
    return None