def handle(args, db):
    sku = args.get("sku")
    if not sku:
        raise ToolError("sku is required.", category="invalid_input")
    prod = db.one("SELECT * FROM products WHERE sku = ?", [sku])
    if not prod:
        raise ToolError(f"Product with SKU {sku} not found.", category="not_found")
    return {
        "sku": prod["sku"],
        "title": prod["title"],
        "category": prod["category"],
        "list_price_cents": prod["list_price_cents"],
        "is_final_sale": bool(prod["is_final_sale"]),
        "returnable": bool(prod["returnable"])
    }
