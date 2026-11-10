-- subscription_billing_desk's world: schema and seed, as the environment builder sealed it for phase-1
-- sheet task V16. Hosted runs seed PostgreSQL from this world.

CREATE TABLE clock (
    now TEXT PRIMARY KEY NOT NULL
);
INSERT INTO "clock" VALUES('2026-10-15T12:00:00Z');
CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "environment_policy" VALUES('timing_mode','"end_of_period"');
INSERT INTO "environment_policy" VALUES('enforce_floor','true');
INSERT INTO "environment_policy" VALUES('require_confirmation','true');
CREATE TABLE invoice_lines (
    line_id TEXT PRIMARY KEY NOT NULL,
    invoice_id TEXT NOT NULL,
    description TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_amount_cents INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    line_type TEXT NOT NULL
);
INSERT INTO "invoice_lines" VALUES('line_01_curr_1','inv_01_curr','Pro Plan Base Subscription',1,19900,19900,'plan_base');
INSERT INTO "invoice_lines" VALUES('line_01_curr_2','inv_01_curr','Additional User Seats (6 seats)',6,2500,15000,'seat_addon');
INSERT INTO "invoice_lines" VALUES('line_01_prev_1','inv_01_prev','Pro Plan Base Subscription',1,19900,19900,'plan_base');
INSERT INTO "invoice_lines" VALUES('line_01_prev_2','inv_01_prev','Additional User Seats (3 seats)',3,2500,7500,'seat_addon');
INSERT INTO "invoice_lines" VALUES('line_02_curr_1','inv_02_curr','Pro Plan Base Subscription',1,19900,19900,'plan_base');
INSERT INTO "invoice_lines" VALUES('line_02_curr_2','inv_02_curr','Additional User Seats (12 seats)',12,2500,30000,'seat_addon');
INSERT INTO "invoice_lines" VALUES('line_02_prev_1','inv_02_prev','Pro Plan Base Subscription',1,19900,19900,'plan_base');
INSERT INTO "invoice_lines" VALUES('line_02_prev_2','inv_02_prev','Additional User Seats (10 seats)',10,2500,25000,'seat_addon');
INSERT INTO "invoice_lines" VALUES('line_03_curr_1','inv_03_curr','Basic Plan Base Subscription',1,7900,7900,'plan_base');
INSERT INTO "invoice_lines" VALUES('line_03_curr_2','inv_03_curr','Additional User Seats (8 seats)',8,1500,12000,'seat_addon');
INSERT INTO "invoice_lines" VALUES('line_04_curr_1','inv_04_curr','Enterprise Plan Base Subscription',1,49900,49900,'plan_base');
INSERT INTO "invoice_lines" VALUES('line_04_curr_2','inv_04_curr','Additional User Seats (80 seats)',80,4000,320000,'seat_addon');
INSERT INTO "invoice_lines" VALUES('line_05_curr_1','inv_05_curr','Starter Plan Base Subscription',1,2900,2900,'plan_base');
INSERT INTO "invoice_lines" VALUES('line_05_curr_2','inv_05_curr','Additional User Seats (3 seats)',3,1000,3000,'seat_addon');
INSERT INTO "invoice_lines" VALUES('line_05_prev_1','inv_05_prev','Starter Plan Base Subscription',1,2900,2900,'plan_base');
INSERT INTO "invoice_lines" VALUES('line_05_prev_2','inv_05_prev','Additional User Seats (2 seats)',2,1000,2000,'seat_addon');
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY NOT NULL,
    tenant_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    subtotal_cents INTEGER NOT NULL,
    tax_cents INTEGER NOT NULL,
    total_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO "invoices" VALUES('inv_01_curr','T01','sub_01','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',34900,3490,38390,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_01_prev','T01','sub_01','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',27400,2740,30140,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_01_hist','T01','sub_01','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',27400,2740,30140,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_02_curr','T02','sub_02','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',49900,4990,54890,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_02_prev','T02','sub_02','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',44900,4490,49390,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_03_curr','T03','sub_03','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',19900,1990,21890,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_03_prev','T03','sub_03','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',19900,1990,21890,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_04_curr','T04','sub_04','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',369900,36990,406890,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_04_prev','T04','sub_04','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',369900,36990,406890,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_05_curr','T05','sub_05','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',5900,590,6490,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_05_prev','T05','sub_05','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',4900,490,5390,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_06_curr','T06','sub_06','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',69900,6990,76890,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_06_prev','T06','sub_06','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',69900,6990,76890,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_07_curr','T07','sub_07','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',15400,1540,16940,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_07_prev','T07','sub_07','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',15400,1540,16940,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_08_curr','T08','sub_08','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',529900,52990,582890,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_08_prev','T08','sub_08','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',529900,52990,582890,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_09_curr','T09','sub_09','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',6900,690,7590,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_09_prev','T09','sub_09','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',6900,690,7590,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_10_curr','T10','sub_10','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',57400,5740,63140,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_10_prev','T10','sub_10','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',57400,5740,63140,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_11_curr','T11','sub_11','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',18400,1840,20240,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_11_prev','T11','sub_11','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',18400,1840,20240,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_12_curr','T12','sub_12','2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',82400,8240,90640,'paid','2026-10-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_12_prev','T12','sub_12','2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',82400,8240,90640,'paid','2026-09-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_02_hist','T02','sub_02','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',44900,4490,49390,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_03_hist','T03','sub_03','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',19900,1990,21890,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_04_hist','T04','sub_04','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',369900,36990,406890,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_05_hist','T05','sub_05','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',4900,490,5390,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_06_hist','T06','sub_06','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',69900,6990,76890,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_07_hist','T07','sub_07','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',15400,1540,16940,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_08_hist','T08','sub_08','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',529900,52990,582890,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_09_hist','T09','sub_09','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',6900,690,7590,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_10_hist','T10','sub_10','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',57400,5740,63140,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_11_hist','T11','sub_11','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',18400,1840,20240,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_12_hist','T12','sub_12','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z',82400,8240,90640,'paid','2026-08-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_01_old','T01','sub_01','2026-07-01T00:00:00Z','2026-08-01T00:00:00Z',27400,2740,30140,'paid','2026-07-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_02_old','T02','sub_02','2026-07-01T00:00:00Z','2026-08-01T00:00:00Z',44900,4490,49390,'paid','2026-07-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_03_old','T03','sub_03','2026-07-01T00:00:00Z','2026-08-01T00:00:00Z',19900,1990,21890,'paid','2026-07-01T00:00:00Z');
INSERT INTO "invoices" VALUES('inv_04_old','T04','sub_04','2026-07-01T00:00:00Z','2026-08-01T00:00:00Z',369900,36990,406890,'paid','2026-07-01T00:00:00Z');
CREATE TABLE plans (
    plan_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    tier TEXT NOT NULL,
    base_price_cents INTEGER NOT NULL,
    seat_price_cents INTEGER NOT NULL,
    max_seats INTEGER NOT NULL,
    features TEXT NOT NULL
);
INSERT INTO "plans" VALUES('starter','Starter','starter',2900,1000,5,'["basic_reporting", "email_support", "community_forum"]');
INSERT INTO "plans" VALUES('basic','Basic','basic',7900,1500,10,'["basic_reporting", "email_support", "community_forum", "csv_export", "standard_integrations"]');
INSERT INTO "plans" VALUES('pro','Pro','pro',19900,2500,50,'["basic_reporting", "email_support", "community_forum", "csv_export", "standard_integrations", "advanced_analytics", "priority_support", "audit_logs", "sso"]');
INSERT INTO "plans" VALUES('enterprise','Enterprise','enterprise',49900,4000,500,'["basic_reporting", "email_support", "community_forum", "csv_export", "standard_integrations", "advanced_analytics", "priority_support", "audit_logs", "sso", "dedicated_csm", "custom_sla", "custom_data_retention"]');
CREATE TABLE scheduled_changes (
    change_id TEXT PRIMARY KEY NOT NULL,
    tenant_id TEXT NOT NULL,
    target_plan_id TEXT NOT NULL,
    target_seats INTEGER NOT NULL,
    effective_at TEXT NOT NULL,
    status TEXT NOT NULL,
    request_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE subscriptions (
    subscription_id TEXT PRIMARY KEY NOT NULL,
    tenant_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    status TEXT NOT NULL,
    seat_count INTEGER NOT NULL,
    current_period_start TEXT NOT NULL,
    current_period_end TEXT NOT NULL,
    cancel_at_period_end BOOLEAN NOT NULL,
    version INTEGER NOT NULL
);
INSERT INTO "subscriptions" VALUES('sub_01','T01','pro','active',6,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_02','T02','pro','active',12,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_03','T03','basic','active',8,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_04','T04','enterprise','active',80,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_05','T05','starter','active',3,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_06','T06','pro','active',20,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_07','T07','basic','active',5,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_08','T08','enterprise','active',120,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_09','T09','starter','active',4,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_10','T10','pro','active',15,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_11','T11','basic','active',7,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_12','T12','pro','active',25,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_13','T01','basic','canceled',3,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_14','T02','basic','canceled',8,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_15','T03','starter','canceled',4,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_16','T04','pro','canceled',45,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_17','T05','starter','canceled',2,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_18','T06','basic','canceled',10,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_19','T07','starter','canceled',3,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',0,1);
INSERT INTO "subscriptions" VALUES('sub_20','T08','pro','canceled',80,'2026-09-01T00:00:00Z','2026-10-01T00:00:00Z',0,1);
CREATE TABLE tenants (
    tenant_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO "tenants" VALUES('T01','Acme Corp','2025-01-10T00:00:00Z');
INSERT INTO "tenants" VALUES('T02','BetaTech','2025-02-15T00:00:00Z');
INSERT INTO "tenants" VALUES('T03','CloudScale','2025-03-01T00:00:00Z');
INSERT INTO "tenants" VALUES('T04','DataFlow','2025-03-20T00:00:00Z');
INSERT INTO "tenants" VALUES('T05','EchoSystems','2025-04-05T00:00:00Z');
INSERT INTO "tenants" VALUES('T06','FinTrack','2025-04-18T00:00:00Z');
INSERT INTO "tenants" VALUES('T07','GrowthLabs','2025-05-12T00:00:00Z');
INSERT INTO "tenants" VALUES('T08','HyperScale','2025-06-01T00:00:00Z');
INSERT INTO "tenants" VALUES('T09','InnoSoft','2025-06-25T00:00:00Z');
INSERT INTO "tenants" VALUES('T10','JumpStart','2025-07-10T00:00:00Z');
INSERT INTO "tenants" VALUES('T11','KiteWorks','2025-08-01T00:00:00Z');
INSERT INTO "tenants" VALUES('T12','LunarOps','2025-09-01T00:00:00Z');
CREATE TABLE usage_records (
    usage_id TEXT PRIMARY KEY NOT NULL,
    tenant_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    current_value INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL
);
INSERT INTO "usage_records" VALUES('use_01','T01','seats',6,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_02','T02','seats',12,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_03','T03','seats',8,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_04','T04','seats',80,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_05','T05','seats',3,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_06','T06','seats',20,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_07','T07','seats',5,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_08','T08','seats',120,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_09','T09','seats',4,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_10','T10','seats',15,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_11','T11','seats',7,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
INSERT INTO "usage_records" VALUES('use_12','T12','seats',25,'2026-10-01T00:00:00Z','2026-11-01T00:00:00Z');
CREATE TABLE users (
    actor_id TEXT PRIMARY KEY NOT NULL,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL
);
INSERT INTO "users" VALUES('U01_ADMIN','T01','Alice Admin','alice@acme.com','billing_admin');
INSERT INTO "users" VALUES('U01_MEMBER','T01','Bob Member','bob@acme.com','member');
INSERT INTO "users" VALUES('U02_ADMIN','T02','Brenda Boss','brenda@betatech.com','billing_admin');
INSERT INTO "users" VALUES('U02_MEMBER','T02','Brian Dev','brian@betatech.com','member');
INSERT INTO "users" VALUES('U03_ADMIN','T03','Carol Cloud','carol@cloudscale.com','billing_admin');
INSERT INTO "users" VALUES('U03_MEMBER','T03','Charlie User','charlie@cloudscale.com','member');
INSERT INTO "users" VALUES('U04_ADMIN','T04','David Data','david@dataflow.com','billing_admin');
INSERT INTO "users" VALUES('U04_MEMBER','T04','Dan Eng','dan@dataflow.com','member');
INSERT INTO "users" VALUES('U05_ADMIN','T05','Eva Echo','eva@echosystems.com','billing_admin');
INSERT INTO "users" VALUES('U05_MEMBER','T05','Eric Ops','eric@echosystems.com','member');
INSERT INTO "users" VALUES('U06_ADMIN','T06','Frank Fin','frank@fintrack.com','billing_admin');
INSERT INTO "users" VALUES('U06_MEMBER','T06','Fiona Analyst','fiona@fintrack.com','member');
INSERT INTO "users" VALUES('U07_ADMIN','T07','Grace Growth','grace@growthlabs.com','billing_admin');
INSERT INTO "users" VALUES('U07_MEMBER','T07','George Marketer','george@growthlabs.com','member');
INSERT INTO "users" VALUES('U08_ADMIN','T08','Helen Hyper','helen@hyperscale.com','billing_admin');
INSERT INTO "users" VALUES('U08_MEMBER','T08','Harry Infra','harry@hyperscale.com','member');
INSERT INTO "users" VALUES('U09_ADMIN','T09','Ian Inno','ian@innosoft.com','billing_admin');
INSERT INTO "users" VALUES('U09_MEMBER','T09','Isla Coder','isla@innosoft.com','member');
INSERT INTO "users" VALUES('U10_ADMIN','T10','Julia Jump','julia@jumpstart.com','billing_admin');
INSERT INTO "users" VALUES('U10_MEMBER','T10','Jack Dev','jack@jumpstart.com','member');
INSERT INTO "users" VALUES('U11_ADMIN','T11','Karen Kite','karen@kiteworks.com','billing_admin');
INSERT INTO "users" VALUES('U11_MEMBER','T11','Kevin Ops','kevin@kiteworks.com','member');
INSERT INTO "users" VALUES('U12_ADMIN','T12','Leo Lunar','leo@lunarops.com','billing_admin');
INSERT INTO "users" VALUES('U12_MEMBER','T12','Lucy SRE','lucy@lunarops.com','member');
