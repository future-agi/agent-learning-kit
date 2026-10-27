-- calendar_scheduling_desk's world: schema and seed, as the environment builder sealed it for phase-1
-- sheet task M03. Hosted runs seed PostgreSQL from this world.

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    target_id TEXT,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
INSERT INTO "approvals" VALUES('APP-1001','alice@company.com','create_event','CAL-PRIMARY','HASH-VALID-1001','approved','2026-10-26T09:00:00Z','2026-10-26T18:00:00Z');
INSERT INTO "approvals" VALUES('APP-RESCHEDULE-01','alice@company.com','update_event','EVT-04','HASH-VALID-RESCHEDULE','approved','2026-10-26T09:00:00Z','2026-10-26T18:00:00Z');
INSERT INTO "approvals" VALUES('APP-DELETE-01','alice@company.com','delete_event','EVT-01','HASH-VALID-DELETE','approved','2026-10-26T09:00:00Z','2026-10-26T18:00:00Z');
CREATE TABLE attendees (
    email TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    time_zone TEXT NOT NULL,
    working_hours_start TEXT NOT NULL,
    working_hours_end TEXT NOT NULL
);
INSERT INTO "attendees" VALUES('alice@company.com','Alice Morgan','America/New_York','09:00','17:00');
INSERT INTO "attendees" VALUES('bob@company.com','Bob Smith','America/New_York','09:00','17:00');
INSERT INTO "attendees" VALUES('charlie@company.com','Charlie Davies','Europe/London','09:00','17:00');
INSERT INTO "attendees" VALUES('diana@company.com','Diana Clark','Europe/London','09:00','17:00');
INSERT INTO "attendees" VALUES('raj@partner.org','Raj Patel','Asia/Kolkata','10:00','18:00');
INSERT INTO "attendees" VALUES('priya@partner.org','Priya Sharma','Asia/Kolkata','09:30','18:30');
CREATE TABLE calendars (
    calendar_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    description TEXT,
    time_zone TEXT NOT NULL,
    access_role TEXT NOT NULL,
    primary_calendar BOOLEAN NOT NULL DEFAULT 0
);
INSERT INTO "calendars" VALUES('CAL-PRIMARY','Primary Calendar','Main user calendar','America/New_York','owner',1);
INSERT INTO "calendars" VALUES('CAL-TEAM','Team Projects','Shared team calendar','America/New_York','writer',0);
INSERT INTO "calendars" VALUES('CAL-RO','Corporate Announcements','Company Holidays & Announcements','America/New_York','reader',0);
CREATE TABLE clock (
    id INTEGER PRIMARY KEY,
    now TEXT NOT NULL
);
INSERT INTO "clock" VALUES(1,'2026-10-26T09:00:00Z');
CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "environment_policy" VALUES('buffer_minutes','10');
INSERT INTO "environment_policy" VALUES('max_horizon_days','60');
INSERT INTO "environment_policy" VALUES('min_notice_hours','2');
INSERT INTO "environment_policy" VALUES('allowed_domains','"*"');
INSERT INTO "environment_policy" VALUES('require_approval_for_mutations','true');
CREATE TABLE event_attendees (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT,
    response_status TEXT NOT NULL DEFAULT 'needsAction',
    is_organizer BOOLEAN NOT NULL DEFAULT 0,
    is_optional BOOLEAN NOT NULL DEFAULT 0
);
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    calendar_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    description TEXT,
    location TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    time_zone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    recurring_event_id TEXT,
    recurrence_rule TEXT,
    original_start_time TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE fault_injections (
    fault_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    target_id TEXT,
    fault_type TEXT NOT NULL,
    trigger_once BOOLEAN NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT 1
);
CREATE TABLE simulated_outbox (
    message_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    payload TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
