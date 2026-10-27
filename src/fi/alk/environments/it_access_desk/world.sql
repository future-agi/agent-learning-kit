-- it_access_desk's world: schema and seed, as the environment builder sealed it for phase-1
-- sheet task V20. Hosted runs seed PostgreSQL from this world.

CREATE TABLE access_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    employee_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    role_name TEXT NOT NULL,
    justification TEXT NOT NULL,
    status TEXT NOT NULL,
    approver_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE applications (
    app_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    category TEXT NOT NULL
);
INSERT INTO "applications" VALUES('APP-RPT','Reporting Application','Internal reporting and business intelligence dashboards','E05','Analytics');
INSERT INTO "applications" VALUES('APP-CRM','CRM System','Customer relationship management and sales lead pipeline','E13','Sales');
INSERT INTO "applications" VALUES('APP-FIN','Financial Ledger','General ledger and corporate financial reporting engine','E06','Finance');
INSERT INTO "applications" VALUES('APP-HRIS','HR Information System','Human resources information and employee directory system','E10','HR');
INSERT INTO "applications" VALUES('APP-PROD','Production Cloud Platform','Production cloud infrastructure and Kubernetes clusters','E02','Infrastructure');
INSERT INTO "applications" VALUES('APP-SEC','Security Monitoring Portal','Enterprise security monitoring and threat detection portal','E04','Security');
INSERT INTO "applications" VALUES('APP-CODE','Source Code Repository','Source code repository and continuous integration platform','E12','Engineering');
INSERT INTO "applications" VALUES('APP-MKT','Marketing Campaign Hub','Marketing campaign management and digital asset hub','E08','Marketing');
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY NOT NULL,
    token TEXT NOT NULL,
    request_id TEXT NOT NULL,
    approver_id TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    role_name TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE audit_log (
    log_id TEXT PRIMARY KEY NOT NULL,
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE TABLE clock (
    now TEXT PRIMARY KEY NOT NULL
);
INSERT INTO "clock" VALUES('2026-10-26T09:00:00Z');
CREATE TABLE employees (
    employee_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    department TEXT NOT NULL,
    manager_id TEXT,
    status TEXT NOT NULL
);
INSERT INTO "employees" VALUES('E01','Alice Smith','alice.smith@example.com','analytics','E05','active');
INSERT INTO "employees" VALUES('E02','Bob Jones','bob.jones@example.com','engineering','E05','active');
INSERT INTO "employees" VALUES('E03','Charlie Brown','charlie.brown@example.com','finance','E06','active');
INSERT INTO "employees" VALUES('E04','Diana Prince','diana.prince@example.com','security','E06','active');
INSERT INTO "employees" VALUES('E05','Evan Wright','evan.wright@example.com','analytics','E07','active');
INSERT INTO "employees" VALUES('E06','Fiona Gallagher','fiona.gallagher@example.com','finance','E07','active');
INSERT INTO "employees" VALUES('E07','George Clark','george.clark@example.com','operations',NULL,'active');
INSERT INTO "employees" VALUES('E08','Hannah Abbott','hannah.abbott@example.com','marketing','E05','active');
INSERT INTO "employees" VALUES('E09','Ian Malcolm','ian.malcolm@example.com','analytics','E05','active');
INSERT INTO "employees" VALUES('E10','Julia Roberts','julia.roberts@example.com','hr','E06','active');
INSERT INTO "employees" VALUES('E11','Kevin Bacon','kevin.bacon@example.com','sales','E05','active');
INSERT INTO "employees" VALUES('E12','Laura Croft','laura.croft@example.com','engineering','E05','active');
INSERT INTO "employees" VALUES('E13','Michael Scott','michael.scott@example.com','sales','E07','active');
INSERT INTO "employees" VALUES('E14','Nina Simone','nina.simone@example.com','legal','E06','active');
INSERT INTO "employees" VALUES('E15','Oscar Martinez','oscar.martinez@example.com','finance','E06','active');
INSERT INTO "employees" VALUES('E16','Pam Beesly','pam.beesly@example.com','operations','E05','active');
INSERT INTO "employees" VALUES('E17','Quinn Fabray','quinn.fabray@example.com','engineering','E05','active');
INSERT INTO "employees" VALUES('E18','Ryan Howard','ryan.howard@example.com','marketing','E05','active');
INSERT INTO "employees" VALUES('E19','Sarah Connor','sarah.connor@example.com','security','E06','active');
INSERT INTO "employees" VALUES('E20','Toby Flenderson','toby.flenderson@example.com','hr','E06','inactive');
CREATE TABLE entitlements (
    entitlement_id TEXT PRIMARY KEY NOT NULL,
    employee_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    role_name TEXT NOT NULL,
    status TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    granted_by TEXT NOT NULL
);
CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "environment_policy" VALUES('sod_enabled','true');
INSERT INTO "environment_policy" VALUES('default_expiry_days','90');
INSERT INTO "environment_policy" VALUES('prevent_duplicate_pending_requests','true');
CREATE TABLE handoff_tickets (
    ticket_id TEXT PRIMARY KEY NOT NULL,
    employee_id TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    details TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE roles (
    role_id TEXT PRIMARY KEY NOT NULL,
    app_id TEXT NOT NULL,
    role_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    description TEXT NOT NULL,
    requires_approval TEXT NOT NULL
);
INSERT INTO "roles" VALUES('ROLE-RPT-VIEWER','APP-RPT','viewer','viewer','Read-only access to business intelligence dashboards','manager');
INSERT INTO "roles" VALUES('ROLE-RPT-EDITOR','APP-RPT','editor','editor','Create and edit reports and queries','app_owner');
INSERT INTO "roles" VALUES('ROLE-RPT-ADMIN','APP-RPT','admin','admin','Full reporting platform administrative privileges','both');
INSERT INTO "roles" VALUES('ROLE-CRM-VIEWER','APP-CRM','viewer','viewer','Read-only access to customer contact records','manager');
INSERT INTO "roles" VALUES('ROLE-CRM-REP','APP-CRM','sales_rep','editor','Manage customer leads and accounts','manager');
INSERT INTO "roles" VALUES('ROLE-CRM-ADMIN','APP-CRM','sales_admin','admin','CRM system administration and user permissions','both');
INSERT INTO "roles" VALUES('ROLE-FIN-VIEWER','APP-FIN','viewer','viewer','Read-only view of financial summaries','manager');
INSERT INTO "roles" VALUES('ROLE-FIN-ACCT','APP-FIN','accountant','editor','General ledger entry creation and ledger editing','app_owner');
INSERT INTO "roles" VALUES('ROLE-FIN-APPR','APP-FIN','finance_approver','admin','Approval authority for corporate transactions and disbursements','both');
INSERT INTO "roles" VALUES('ROLE-HRIS-EMP','APP-HRIS','employee','viewer','Standard employee self-service view','none');
INSERT INTO "roles" VALUES('ROLE-HRIS-SPEC','APP-HRIS','hr_specialist','editor','Manage HR records and employee onboarding files','app_owner');
INSERT INTO "roles" VALUES('ROLE-HRIS-ADMIN','APP-HRIS','hr_admin','admin','Full HR system administration and sensitive compensation access','both');
INSERT INTO "roles" VALUES('ROLE-PROD-READONLY','APP-PROD','readonly','viewer','Read-only monitoring and log viewing','manager');
INSERT INTO "roles" VALUES('ROLE-PROD-DEPLOYER','APP-PROD','deployer','editor','Deploy applications to production clusters','app_owner');
INSERT INTO "roles" VALUES('ROLE-PROD-ADMIN','APP-PROD','cluster_admin','admin','Full cluster root and configuration administrative control','both');
INSERT INTO "roles" VALUES('ROLE-SEC-AUDITOR','APP-SEC','auditor','viewer','Read-only compliance and audit log viewing','manager');
INSERT INTO "roles" VALUES('ROLE-SEC-ANALYST','APP-SEC','analyst','editor','Investigate alerts and modify detection rules','app_owner');
INSERT INTO "roles" VALUES('ROLE-SEC-ADMIN','APP-SEC','sec_admin','admin','Security portal administration and policy configuration','both');
INSERT INTO "roles" VALUES('ROLE-CODE-READ','APP-CODE','read','viewer','Read and clone repositories','manager');
INSERT INTO "roles" VALUES('ROLE-CODE-WRITE','APP-CODE','write','editor','Push commits and create branches','manager');
INSERT INTO "roles" VALUES('ROLE-CODE-MAINTAINER','APP-CODE','maintainer','admin','Repository settings, branch protection, and administrative control','app_owner');
INSERT INTO "roles" VALUES('ROLE-MKT-VIEWER','APP-MKT','viewer','viewer','Read-only marketing asset viewing','manager');
INSERT INTO "roles" VALUES('ROLE-MKT-AUTHOR','APP-MKT','author','editor','Publish and modify marketing campaign materials','manager');
INSERT INTO "roles" VALUES('ROLE-MKT-ADMIN','APP-MKT','mkt_admin','admin','Marketing hub administration and integration settings','both');
CREATE TABLE sod_rules (
    rule_id TEXT PRIMARY KEY NOT NULL,
    app_id TEXT NOT NULL,
    role_a TEXT NOT NULL,
    role_b TEXT NOT NULL,
    description TEXT NOT NULL
);
INSERT INTO "sod_rules" VALUES('SOD-01','APP-FIN','accountant','finance_approver','An accountant cannot hold finance approver authority');
INSERT INTO "sod_rules" VALUES('SOD-02','APP-PROD','deployer','cluster_admin','Deployers cannot hold cluster administrator access');
INSERT INTO "sod_rules" VALUES('SOD-03','APP-SEC','auditor','analyst','Security auditors cannot hold active analyst privileges');
INSERT INTO "sod_rules" VALUES('SOD-04','APP-HRIS','hr_specialist','hr_admin','HR specialists cannot hold full HR administrator access');
