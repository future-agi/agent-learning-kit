# Shared by the generator and copied verbatim into every handler that handles time.


def _appointment(db, appointment_id, customer_id):
    row = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [appointment_id])
    if row is None:
        raise ToolError(f"Appointment {appointment_id!r} not found.", category="not_found")
    if row["customer_id"] != customer_id:
        raise ToolError(f"Appointment {appointment_id} does not belong to customer {customer_id}.", category="permission_denied")
    return row


def _view(a):
    return {k: a[k] for k in ("appointment_id", "customer_id", "service_id", "staff_id", "location_id", "start_utc", "end_utc", "local_start", "utc_offset", "time_zone", "status", "calendar_event_id", "rescheduled_from", "late_fee_cents")}


def handle(args, db):
    return _view(_appointment(db, args.get("appointment_id"), args.get("customer_id")))
