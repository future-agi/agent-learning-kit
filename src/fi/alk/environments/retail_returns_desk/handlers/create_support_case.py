def handle(args, db):
    customer_id = args.get("customer_id")
    subject = args.get("subject")
    details = args.get("details")
    order_id = args.get("order_id")
    
    if not customer_id or not subject or not details:
        raise ToolError("customer_id, subject, and details are required.", category="invalid_input")
        
    cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if not cust:
        raise ToolError(f"Customer {customer_id} not found.", category="not_found")
        
    if order_id:
        ord_row = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
        if not ord_row:
            raise ToolError(f"Order {order_id} not found.", category="not_found")
            
    clock_row = db.one("SELECT now FROM clock")
    created_at = clock_row["now"]
    
    case_count = db.one("SELECT COUNT(*) as count FROM support_cases")["count"]
    case_id = f"SC{case_count + 1:02d}"
    while db.one("SELECT * FROM support_cases WHERE case_id = ?", [case_id]):
        case_count += 1
        case_id = f"SC{case_count + 1:02d}"
        
    db.execute(
        "INSERT INTO support_cases (case_id, customer_id, order_id, subject, details, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [case_id, customer_id, order_id, subject, details, "opened", created_at]
    )
    
    return {
        "case_id": case_id,
        "customer_id": customer_id,
        "order_id": order_id,
        "subject": subject,
        "details": details,
        "status": "opened",
        "created_at": created_at
    }
