-- notion_action_tracker's world: schema and seed, as the environment builder sealed it for phase-1
-- sheet task M05. Hosted runs seed PostgreSQL from this world.

CREATE TABLE action_records (
    action_id TEXT PRIMARY KEY,
    database_id TEXT NOT NULL,
    title TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL,
    source_page_id TEXT NOT NULL,
    external_key TEXT NOT NULL
);
CREATE TABLE async_tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    operation TEXT NOT NULL,
    result_json TEXT NOT NULL,
    error_message TEXT NOT NULL
);
INSERT INTO "async_tasks" VALUES('task_init_01','completed','notion_create_action_record','{"action_id": "act_01"}','');
INSERT INTO "async_tasks" VALUES('task_failed_01','failed','notion_create_action_record','{}','Database write timed out after retry');
CREATE TABLE audit_log (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE clock (
    now TEXT PRIMARY KEY
);
INSERT INTO "clock" VALUES('2026-10-26T10:00:00Z');
CREATE TABLE databases (
    database_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    allowed_access BOOLEAN NOT NULL
);
INSERT INTO "databases" VALUES('db_action_tracker','Action Tracker Database','{"properties": {"title": {"type": "title"}, "owner": {"type": "people"}, "due_date": {"type": "date"}, "status": {"type": "select", "options": ["Not Started", "In Progress", "Completed", "Blocked"]}, "source_page": {"type": "relation"}}, "required": ["title", "owner", "due_date", "status", "source_page"]}',1);
INSERT INTO "databases" VALUES('db_restricted_finance','Restricted Executive Financials','{"properties": {"title": {"type": "title"}, "amount": {"type": "number"}}, "required": ["title"]}',0);
CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "environment_policy" VALUES('allowed_database_id','"db_action_tracker"');
INSERT INTO "environment_policy" VALUES('require_approval_for_writes','true');
INSERT INTO "environment_policy" VALUES('default_status','"Not Started"');
CREATE TABLE pages (
    page_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content_markdown TEXT NOT NULL,
    created_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    archived BOOLEAN NOT NULL,
    allowed_access BOOLEAN NOT NULL
);
INSERT INTO "pages" VALUES('N-01','Sprint 42 Retrospective','# Sprint 42 Retrospective

## Discussion
- Velocity improved by 15%.
- Need to streamline PR reviews.

## Agreed Actions
- Sarah Connor to publish new PR review checklist by 2026-10-28.
- Marcus Wright to archive stale branches by 2026-10-29.','2026-10-20T14:00:00Z','America/New_York',0,1);
INSERT INTO "pages" VALUES('N-02','Q4 Product Strategy','# Q4 Product Strategy

## Strategic Pillars
- AI-driven automation
- Security and compliance

## Next Steps
- Elena Rostova to draft SOC2 roadmap by 2026-11-15.','2026-10-21T10:00:00Z','America/New_York',0,1);
INSERT INTO "pages" VALUES('N-03','Architecture Sync - Auth Migration','# Architecture Sync - Auth Migration

## Decisions
- Migrate to OAuth 2.1.
- Deprecate API keys by Q1.

## Commitments
- Alex Chen to update token validation middleware by 2026-11-01.','2026-10-22T16:00:00Z','America/New_York',0,1);
INSERT INTO "pages" VALUES('N-04','Weekly Leadership Sync','# Weekly Leadership Sync

## Attendees
- Alex Rivera, Sarah Connor, Marcus Wright

## Agreed Commitments
- [ACTION] Alex Rivera: Update SLA documentation for enterprise tier by 2026-10-30.
- [ACTION] Sarah Connor: Review engineering hiring pipeline by 2026-11-02.
- [ACTION] Marcus Wright: Benchmark database queries under heavy load by 2026-11-05.','2026-10-24T09:30:00Z','America/New_York',0,1);
INSERT INTO "pages" VALUES('N-05','Brainstorming: Mobile App Redesign','# Brainstorming: Mobile App Redesign

## Ideas Discussed
- We could maybe experiment with dark mode as default for mobile.
- We might consider adding biometric login if users request it.

## Confirmed Next Steps
- Elena Rostova to finalize Figma UI mockups by 2026-10-31.','2026-10-24T15:00:00Z','America/New_York',0,1);
INSERT INTO "pages" VALUES('N-06','Operations Triage','# Operations Triage

## Discussion
- Incident response time was slightly delayed on Saturday.

## Action Items
- Alex: Conduct post-incident root cause analysis by 2026-10-29.','2026-10-25T11:00:00Z','America/New_York',0,1);
INSERT INTO "pages" VALUES('N-07','Security Incident Post-Mortem','# Security Incident Post-Mortem

## Summary
- Transient network glitch resolved.

## Action Items (Imported)
- Marcus Wright to update firewall rules by 2026-10-27.
- Sarah Connor to rotate root certificates by 2026-10-28.
- Alex Rivera to notify compliance officer by 2026-10-29.','2026-10-25T13:00:00Z','America/New_York',0,1);
INSERT INTO "pages" VALUES('N-08','Archived 2025 Planning','# Archived 2025 Planning

## Legacy Items
- Old planning notes. Archived and read-only.','2025-01-10T10:00:00Z','America/New_York',1,1);
CREATE TABLE workspace_users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL
);
INSERT INTO "workspace_users" VALUES('user_alex_rivera','Alex Rivera','alex.rivera@acme.corp','member','active');
INSERT INTO "workspace_users" VALUES('user_alex_chen','Alex Chen','alex.chen@acme.corp','member','active');
INSERT INTO "workspace_users" VALUES('user_sarah_connor','Sarah Connor','sarah.connor@acme.corp','lead','active');
INSERT INTO "workspace_users" VALUES('user_marcus_wright','Marcus Wright','marcus.wright@acme.corp','member','active');
INSERT INTO "workspace_users" VALUES('user_dana_lee','Dana Lee','dana.lee@acme.corp','viewer','read_only');
INSERT INTO "workspace_users" VALUES('user_elena_rostova','Elena Rostova','elena.rostova@acme.corp','admin','active');
