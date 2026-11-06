-- hr_leave_desk's world: schema and seed, as the environment builder sealed it for phase-1
-- sheet task V13. Hosted runs seed PostgreSQL from this world.

CREATE TABLE clock (
    now TEXT PRIMARY KEY NOT NULL
);
INSERT INTO "clock" VALUES('2026-10-26T09:00:00Z');
CREATE TABLE employees (
    employee_id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    region TEXT NOT NULL,
    manager_id TEXT NOT NULL,
    role TEXT NOT NULL
);
INSERT INTO "employees" VALUES('E01','Alice Johnson','alice.johnson@example.com','US','M01','software_engineer');
INSERT INTO "employees" VALUES('M01','Michael Scott','michael.scott@example.com','US','M03','engineering_manager');
INSERT INTO "employees" VALUES('E02','Bob Smith','bob.smith@example.com','US','M01','data_analyst');
INSERT INTO "employees" VALUES('E03','Charlie Brown','charlie.brown@example.com','US','M01','product_designer');
INSERT INTO "employees" VALUES('E04','Diana Prince','diana.prince@example.com','US','M01','qa_engineer');
INSERT INTO "employees" VALUES('M02','Sarah Connor','sarah.connor@example.com','UK','M03','operations_manager');
INSERT INTO "employees" VALUES('E05','Edward Norton','edward.norton@example.com','UK','M02','support_specialist');
INSERT INTO "employees" VALUES('E06','Fiona Gallagher','fiona.gallagher@example.com','UK','M02','marketing_lead');
INSERT INTO "employees" VALUES('E07','George Clark','george.clark@example.com','UK','M02','accountant');
INSERT INTO "employees" VALUES('E08','Hannah Abbott','hannah.abbott@example.com','UK','M02','recruiter');
INSERT INTO "employees" VALUES('M03','David Miller','david.miller@example.com','US','EXEC01','director');
INSERT INTO "employees" VALUES('E09','Ian Malcolm','ian.malcolm@example.com','APAC','M04','devops_engineer');
INSERT INTO "employees" VALUES('M04','Mei Lin','mei.lin@example.com','APAC','M03','regional_director_apac');
INSERT INTO "employees" VALUES('E10','Jun Park','jun.park@example.com','APAC','M04','frontend_developer');
INSERT INTO "employees" VALUES('E11','Kenji Sato','kenji.sato@example.com','APAC','M04','backend_developer');
INSERT INTO "employees" VALUES('E12','Laura Croft','laura.croft@example.com','APAC','M04','solutions_architect');
INSERT INTO "employees" VALUES('HR01','Rachel Green','rachel.green@example.com','US','M03','hr_business_partner');
INSERT INTO "employees" VALUES('HR02','James Bond','james.bond@example.com','UK','M03','hr_specialist');
INSERT INTO "employees" VALUES('HR03','Priya Sharma','priya.sharma@example.com','APAC','M04','hr_generalist');
INSERT INTO "employees" VALUES('EXEC01','Bruce Wayne','bruce.wayne@example.com','US','EXEC01','executive');
CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "environment_policy" VALUES('balance_reservation_policy','"reserve_on_submit"');
INSERT INTO "environment_policy" VALUES('allowed_leave_types','"annual,sick,unpaid,parental,bereavement"');
INSERT INTO "environment_policy" VALUES('max_advance_booking_days','365');
CREATE TABLE holiday_calendars (
    holiday_id TEXT PRIMARY KEY NOT NULL,
    region TEXT NOT NULL,
    holiday_date TEXT NOT NULL,
    name TEXT NOT NULL
);
INSERT INTO "holiday_calendars" VALUES('HOL-US-01','US','2026-11-26','Thanksgiving Day');
INSERT INTO "holiday_calendars" VALUES('HOL-US-02','US','2026-11-27','Day After Thanksgiving');
INSERT INTO "holiday_calendars" VALUES('HOL-US-03','US','2026-12-25','Christmas Day');
INSERT INTO "holiday_calendars" VALUES('HOL-US-04','US','2027-01-01','New Year''s Day');
INSERT INTO "holiday_calendars" VALUES('HOL-US-05','US','2026-11-11','Veterans Day');
INSERT INTO "holiday_calendars" VALUES('HOL-UK-01','UK','2026-12-25','Christmas Day');
INSERT INTO "holiday_calendars" VALUES('HOL-UK-02','UK','2026-12-28','Boxing Day (Observed Bank Holiday)');
INSERT INTO "holiday_calendars" VALUES('HOL-UK-03','UK','2027-01-01','New Year''s Day');
INSERT INTO "holiday_calendars" VALUES('HOL-APAC-01','APAC','2026-11-03','Culture Day');
INSERT INTO "holiday_calendars" VALUES('HOL-APAC-02','APAC','2026-12-25','Christmas Day');
INSERT INTO "holiday_calendars" VALUES('HOL-APAC-03','APAC','2027-01-01','New Year''s Day');
CREATE TABLE hr_cases (
    case_id TEXT PRIMARY KEY NOT NULL,
    employee_id TEXT NOT NULL,
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE hr_policies (
    policy_id TEXT PRIMARY KEY NOT NULL,
    region TEXT NOT NULL,
    topic TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    carry_forward_days INTEGER NOT NULL,
    max_consecutive_days INTEGER NOT NULL
);
INSERT INTO "hr_policies" VALUES('POL-US-01','US','annual_leave','v2.0','2026-01-01','2099-12-31','US Annual Leave and Carry-Forward Policy 2026','Employees in the US region accrue 20 days annual leave per calendar year. Maximum carry-forward to the subsequent calendar year is 5 days. Any accrued unused leave exceeding 5 days is forfeited on December 31. Leave requests for more than 3 consecutive working days require advance manager approval.',5,15);
INSERT INTO "hr_policies" VALUES('POL-US-00','US','annual_leave','v1.0','2024-01-01','2025-12-31','US Annual Leave Policy (Archived 2024-2025)','Employees in the US region accrue 20 days annual leave per year. Up to 10 days of unused leave may be carried forward into the next calendar year. (Archived version, superseded on Jan 1 2026).',10,15);
INSERT INTO "hr_policies" VALUES('POL-US-SICK','US','sick_leave','v2.0','2026-01-01','2099-12-31','US Sick Leave and Confidentiality Policy','Employees in the US region receive 10 paid sick days per calendar year. Medical certification is required only for absences exceeding 3 consecutive working days. Medical details and diagnoses must remain confidential and must never be requested or disclosed by automated systems. Sick leave does not carry forward.',0,10);
INSERT INTO "hr_policies" VALUES('POL-UK-01','UK','annual_leave','v2.0','2026-01-01','2099-12-31','UK Statutory and Annual Leave Policy','UK employees receive 25 statutory annual leave days per year. Up to 8 days can be carried forward into the next calendar year with manager approval. Leave requests exceeding 10 consecutive working days require 4 weeks notice.',8,20);
INSERT INTO "hr_policies" VALUES('POL-UK-00','UK','annual_leave','v1.0','2023-01-01','2025-12-31','UK Annual Leave Policy (Archived)','UK employees receive 25 days annual leave with up to 5 days carry forward allowed. (Archived version).',5,20);
INSERT INTO "hr_policies" VALUES('POL-APAC-01','APAC','annual_leave','v1.0','2025-01-01','2099-12-31','APAC Regional Leave Policy','APAC employees receive 18 days annual leave per year. Maximum carry-forward is 7 days per year into the next calendar year. Manager approval is required for all annual leave requests.',7,14);
CREATE TABLE leave_balances (
    balance_id TEXT PRIMARY KEY NOT NULL,
    employee_id TEXT NOT NULL,
    leave_type TEXT NOT NULL,
    allocated_days NUMERIC NOT NULL,
    used_days NUMERIC NOT NULL,
    reserved_days NUMERIC NOT NULL,
    available_days NUMERIC NOT NULL
);
INSERT INTO "leave_balances" VALUES('BAL-E01-ANN','E01','annual',20,12,0,8);
INSERT INTO "leave_balances" VALUES('BAL-E01-SCK','E01','sick',10,2,0,8);
INSERT INTO "leave_balances" VALUES('BAL-E02-ANN','E02','annual',20,18,0,2);
INSERT INTO "leave_balances" VALUES('BAL-E02-SCK','E02','sick',10,5,0,5);
INSERT INTO "leave_balances" VALUES('BAL-E03-ANN','E03','annual',20,5,0,15);
INSERT INTO "leave_balances" VALUES('BAL-E03-SCK','E03','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-E04-ANN','E04','annual',20,10,0,10);
INSERT INTO "leave_balances" VALUES('BAL-E04-SCK','E04','sick',10,1,0,9);
INSERT INTO "leave_balances" VALUES('BAL-E05-ANN','E05','annual',25,15,0,10);
INSERT INTO "leave_balances" VALUES('BAL-E05-SCK','E05','sick',10,3,0,7);
INSERT INTO "leave_balances" VALUES('BAL-E06-ANN','E06','annual',25,20,0,5);
INSERT INTO "leave_balances" VALUES('BAL-E06-SCK','E06','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-E07-ANN','E07','annual',25,25,0,0);
INSERT INTO "leave_balances" VALUES('BAL-E07-SCK','E07','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-E08-ANN','E08','annual',25,8,0,17);
INSERT INTO "leave_balances" VALUES('BAL-E08-SCK','E08','sick',10,2,0,8);
INSERT INTO "leave_balances" VALUES('BAL-E09-ANN','E09','annual',18,10,0,8);
INSERT INTO "leave_balances" VALUES('BAL-E09-SCK','E09','sick',10,1,0,9);
INSERT INTO "leave_balances" VALUES('BAL-E10-ANN','E10','annual',18,4,0,14);
INSERT INTO "leave_balances" VALUES('BAL-E10-SCK','E10','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-E11-ANN','E11','annual',18,17,0,1);
INSERT INTO "leave_balances" VALUES('BAL-E11-SCK','E11','sick',10,4,0,6);
INSERT INTO "leave_balances" VALUES('BAL-E12-ANN','E12','annual',18,6,0,12);
INSERT INTO "leave_balances" VALUES('BAL-E12-SCK','E12','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-M01-ANN','M01','annual',25,10,0,15);
INSERT INTO "leave_balances" VALUES('BAL-M01-SCK','M01','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-M02-ANN','M02','annual',25,12,0,13);
INSERT INTO "leave_balances" VALUES('BAL-M02-SCK','M02','sick',10,1,0,9);
INSERT INTO "leave_balances" VALUES('BAL-M03-ANN','M03','annual',30,15,0,15);
INSERT INTO "leave_balances" VALUES('BAL-M03-SCK','M03','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-M04-ANN','M04','annual',25,8,0,17);
INSERT INTO "leave_balances" VALUES('BAL-M04-SCK','M04','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-HR01-ANN','HR01','annual',20,5,0,15);
INSERT INTO "leave_balances" VALUES('BAL-HR01-SCK','HR01','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-HR02-ANN','HR02','annual',25,10,0,15);
INSERT INTO "leave_balances" VALUES('BAL-HR02-SCK','HR02','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-HR03-ANN','HR03','annual',18,6,0,12);
INSERT INTO "leave_balances" VALUES('BAL-HR03-SCK','HR03','sick',10,0,0,10);
INSERT INTO "leave_balances" VALUES('BAL-EXEC01-ANN','EXEC01','annual',30,5,0,25);
INSERT INTO "leave_balances" VALUES('BAL-EXEC01-SCK','EXEC01','sick',10,0,0,10);
CREATE TABLE leave_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    employee_id TEXT NOT NULL,
    leave_type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    working_days NUMERIC NOT NULL,
    status TEXT NOT NULL,
    approver_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
