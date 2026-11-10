def setup(world):
    world.put("customers", {
        "customer_id": "C451",
        "name": "Wanda Maximoff",
        "email": "wanda.maximoff@example.com",
        "phone": "+15550451",
        "postal_code": "98101",
        "address": "12 Old Oak Lane",
        "store_credit_cents": 0
    })
    world.put("orders", {
        "order_id": "O451",
        "customer_id": "C451",
        "order_date": "2026-10-25T10:00:00Z",
        "delivery_date": None,
        "status": "processing",
        "currency": "USD",
        "total_gross_cents": 6400,
        "total_discount_cents": 0,
        "total_tax_cents": 0,
        "total_net_cents": 6400,
        "shipping_address": "12 Old Oak Lane, 98101",
        "cancelled_at": None
    })
    world.put("order_items", {
        "item_id": "IT451",
        "order_id": "O451",
        "sku": "AP02",
        "quantity": 1,
        "unit_price_cents": 6400,
        "line_discount_cents": 0,
        "line_tax_cents": 0,
        "line_net_cents": 6400,
        "returned_quantity": 0
    })
