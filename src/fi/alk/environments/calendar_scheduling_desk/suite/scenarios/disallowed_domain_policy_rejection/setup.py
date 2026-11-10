def setup(world):
    world.put("attendees", {
        "email": "chloe@company.com",
        "display_name": "Chloe Vance",
        "time_zone": "Australia/Sydney",
        "working_hours_start": "09:00:00",
        "working_hours_end": "17:00:00"
    })
    world.put("attendees", {
        "email": "vendor@untrusted-external.net",
        "display_name": "Vendor Partner",
        "time_zone": "America/New_York",
        "working_hours_start": "09:00:00",
        "working_hours_end": "17:00:00"
    })