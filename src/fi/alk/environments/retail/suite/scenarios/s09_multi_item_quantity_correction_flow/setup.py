def setup(world):
    # Iris bought two Insulated Winter Parkas on O09, so starting to return two and settling on one
    # is a correction the order can hold: the line, the order and its invoice all count two.
    world.change("order_items", "IT09", {"quantity": 2, "line_net_cents": 29000}, by="item_id")
    world.change("orders", "O09", {"total_gross_cents": 29000, "total_net_cents": 29000}, by="order_id")
    world.change("invoices", "INV09", {"total_cents": 29000}, by="invoice_id")
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C09"}, by="id")
