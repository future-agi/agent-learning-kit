-- lost_card_and_dispute_desk's world: schema and seed, as the environment builder sealed it for phase-1
-- sheet task V01. Hosted runs seed PostgreSQL from this world.

CREATE TABLE accounts (
    account_id TEXT PRIMARY KEY NOT NULL,
    customer_id TEXT NOT NULL,
    account_number TEXT NOT NULL,
    account_type TEXT NOT NULL,
    balance INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL
);
INSERT INTO "accounts" VALUES('ACC01','C01','1000000001','checking',524000,'USD','active');
INSERT INTO "accounts" VALUES('ACC02','C01','1000000002','savings',1250000,'USD','active');
INSERT INTO "accounts" VALUES('ACC03','C02','1000000003','checking',310000,'USD','active');
INSERT INTO "accounts" VALUES('ACC04','C03','1000000004','checking',89000,'USD','active');
INSERT INTO "accounts" VALUES('ACC05','C03','1000000005','savings',450000,'USD','active');
INSERT INTO "accounts" VALUES('ACC06','C04','1000000006','checking',780000,'USD','active');
INSERT INTO "accounts" VALUES('ACC07','C05','1000000007','checking',150000,'USD','active');
INSERT INTO "accounts" VALUES('ACC08','C05','1000000008','credit',-45000,'USD','active');
INSERT INTO "accounts" VALUES('ACC09','C06','1000000009','checking',920000,'USD','active');
INSERT INTO "accounts" VALUES('ACC10','C07','1000000010','checking',240000,'USD','active');
INSERT INTO "accounts" VALUES('ACC11','C07','1000000011','savings',610000,'USD','active');
INSERT INTO "accounts" VALUES('ACC12','C08','1000000012','checking',430000,'USD','active');
INSERT INTO "accounts" VALUES('ACC13','C09','1000000013','checking',180000,'USD','active');
INSERT INTO "accounts" VALUES('ACC14','C09','1000000014','credit',-12000,'USD','active');
INSERT INTO "accounts" VALUES('ACC15','C10','1000000015','checking',550000,'USD','active');
INSERT INTO "accounts" VALUES('ACC16','C11','1000000016','checking',320000,'USD','active');
INSERT INTO "accounts" VALUES('ACC17','C11','1000000017','savings',890000,'USD','active');
INSERT INTO "accounts" VALUES('ACC18','C12','1000000018','checking',670000,'USD','active');
CREATE TABLE action_log (
    log_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE cards (
    card_id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    card_number_masked TEXT NOT NULL,
    last_four TEXT NOT NULL,
    card_type TEXT NOT NULL,
    status TEXT NOT NULL,
    expiration_date TEXT NOT NULL,
    locked_at TEXT
);
INSERT INTO "cards" VALUES('CARD01','ACC01','C01','••••••••••••4821','4821','debit','active','2028-12-31',NULL);
INSERT INTO "cards" VALUES('CARD02','ACC01','C01','••••••••••••9012','9012','debit','active','2027-06-30',NULL);
INSERT INTO "cards" VALUES('CARD03','ACC03','C02','••••••••••••1123','1123','debit','active','2028-01-31',NULL);
INSERT INTO "cards" VALUES('CARD04','ACC03','C02','••••••••••••2234','2234','debit','active','2029-03-31',NULL);
INSERT INTO "cards" VALUES('CARD05','ACC04','C03','••••••••••••3345','3345','debit','active','2027-11-30',NULL);
INSERT INTO "cards" VALUES('CARD06','ACC05','C03','••••••••••••4456','4456','debit','locked','2026-08-31','2026-10-01T10:00:00Z');
INSERT INTO "cards" VALUES('CARD07','ACC06','C04','••••••••••••5567','5567','debit','active','2028-05-31',NULL);
INSERT INTO "cards" VALUES('CARD08','ACC06','C04','••••••••••••6678','6678','credit','active','2029-09-30',NULL);
INSERT INTO "cards" VALUES('CARD09','ACC07','C05','••••••••••••7789','7789','debit','active','2027-04-30',NULL);
INSERT INTO "cards" VALUES('CARD10','ACC08','C05','••••••••••••8890','8890','credit','active','2028-10-31',NULL);
INSERT INTO "cards" VALUES('CARD11','ACC09','C06','••••••••••••9901','9901','debit','active','2029-01-31',NULL);
INSERT INTO "cards" VALUES('CARD12','ACC09','C06','••••••••••••1012','1012','credit','active','2027-07-31',NULL);
INSERT INTO "cards" VALUES('CARD13','ACC10','C07','••••••••••••2123','2123','debit','active','2028-02-28',NULL);
INSERT INTO "cards" VALUES('CARD14','ACC11','C07','••••••••••••3234','3234','debit','active','2029-12-31',NULL);
INSERT INTO "cards" VALUES('CARD15','ACC12','C08','••••••••••••4345','4345','debit','active','2027-09-30',NULL);
INSERT INTO "cards" VALUES('CARD16','ACC12','C08','••••••••••••5456','5456','credit','active','2028-08-31',NULL);
INSERT INTO "cards" VALUES('CARD17','ACC13','C09','••••••••••••6567','6567','debit','active','2029-04-30',NULL);
INSERT INTO "cards" VALUES('CARD18','ACC14','C09','••••••••••••7678','7678','credit','active','2027-10-31',NULL);
INSERT INTO "cards" VALUES('CARD19','ACC15','C10','••••••••••••8789','8789','debit','active','2028-06-30',NULL);
INSERT INTO "cards" VALUES('CARD20','ACC15','C10','••••••••••••9890','9890','credit','active','2029-11-30',NULL);
INSERT INTO "cards" VALUES('CARD21','ACC16','C11','••••••••••••0901','0901','debit','active','2027-12-31',NULL);
INSERT INTO "cards" VALUES('CARD22','ACC17','C11','••••••••••••1212','1212','debit','locked','2028-03-31','2026-09-15T14:30:00Z');
INSERT INTO "cards" VALUES('CARD23','ACC18','C12','••••••••••••2323','2323','debit','active','2029-07-31',NULL);
INSERT INTO "cards" VALUES('CARD24','ACC18','C12','••••••••••••3434','3434','credit','active','2028-04-30',NULL);
CREATE TABLE clock (
    now TEXT NOT NULL
);
INSERT INTO "clock" VALUES('2026-10-26T12:00:00Z');
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    passcode TEXT NOT NULL,
    kba_answer TEXT NOT NULL,
    otp_code TEXT NOT NULL
);
INSERT INTO "customers" VALUES('C01','Alice','Smith','alice.smith@example.com','+15551234001','8194','Rover','482903');
INSERT INTO "customers" VALUES('C02','Bob','Smith','bob.smith@example.com','+15551234002','5371','Fluffy','918274');
INSERT INTO "customers" VALUES('C03','Charlie','Johnson','charlie.j@example.com','+15551234003','2948','Max','374829');
INSERT INTO "customers" VALUES('C04','Diana','Johnson','diana.j@example.com','+15551234004','7402','Bella','830192');
INSERT INTO "customers" VALUES('C05','Evan','Davis','evan.d@example.com','+15551234005','1839','Luna','592014');
INSERT INTO "customers" VALUES('C06','Fiona','Miller','fiona.m@example.com','+15551234006','6295','Charlie','740182');
INSERT INTO "customers" VALUES('C07','George','Miller','george.m@example.com','+15551234007','3917','Lucy','284019');
INSERT INTO "customers" VALUES('C08','Hannah','Wilson','hannah.w@example.com','+15551234008','9482','Cooper','619284');
INSERT INTO "customers" VALUES('C09','Ian','Taylor','ian.t@example.com','+15551234009','4028','Bailey','849201');
INSERT INTO "customers" VALUES('C10','Julia','Anderson','julia.a@example.com','+15551234010','7163','Daisy','194820');
INSERT INTO "customers" VALUES('C11','Kevin','Thomas','kevin.t@example.com','+15551234011','5820','Sadie','928371');
INSERT INTO "customers" VALUES('C12','Laura','Martinez','laura.m@example.com','+15551234012','3619','Molly','401928');
CREATE TABLE disputes (
    dispute_id TEXT PRIMARY KEY NOT NULL,
    transaction_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    dispute_reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    request_key TEXT,
    notes TEXT
);
CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "environment_policy" VALUES('verification_method','"passcode"');
INSERT INTO "environment_policy" VALUES('max_failed_attempts','3');
INSERT INTO "environment_policy" VALUES('allow_pending_disputes','false');
INSERT INTO "environment_policy" VALUES('max_dispute_age_days','60');
INSERT INTO "environment_policy" VALUES('allowed_reasons','"unrecognized_charge,incorrect_amount,duplicate_charge,goods_not_received"');
INSERT INTO "environment_policy" VALUES('require_confirmation','true');
INSERT INTO "environment_policy" VALUES('retry_limit','2');
CREATE TABLE handoffs (
    handoff_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    department TEXT NOT NULL,
    reason TEXT NOT NULL,
    context TEXT,
    card_id TEXT,
    transaction_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    customer_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    is_verified BOOLEAN NOT NULL,
    verified_at TEXT,
    failed_attempts INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO "sessions" VALUES('SESS01','C01','chat',1,'2026-10-26T11:55:00Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS02','C02','chat',0,NULL,0,'2026-10-26T11:52:00Z');
INSERT INTO "sessions" VALUES('SESS03','C03','chat',1,'2026-10-26T11:52:00Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS04','C04','chat',1,'2026-10-26T11:53:00Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS05','C05','chat',1,'2026-10-26T11:51:00Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS06','C06','chat',1,'2026-10-26T11:54:00Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS07','C07','chat',1,'2026-10-26T11:52:00Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS08','C08','chat',1,'2026-10-26T11:50:30Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS09','C09','chat',1,'2026-10-26T11:51:15Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS10','C10','chat',1,'2026-10-26T11:53:45Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS11','C11','chat',1,'2026-10-26T11:54:10Z',0,'2026-10-26T11:50:00Z');
INSERT INTO "sessions" VALUES('SESS12','C12','chat',1,'2026-10-26T11:52:50Z',0,'2026-10-26T11:50:00Z');
CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    merchant_name TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    transaction_time TEXT NOT NULL,
    description TEXT NOT NULL
);
INSERT INTO "transactions" VALUES('TX01','ACC01','CARD01','Whole Foods Market',4500,'USD','posted','2026-10-14T10:30:00Z','Grocery Store');
INSERT INTO "transactions" VALUES('TX02','ACC01','CARD01','Starbucks',1520,'USD','posted','2026-10-15T08:15:00Z','Coffee Shop');
INSERT INTO "transactions" VALUES('TX03','ACC03','CARD03','Netflix',1299,'USD','posted','2026-10-09T12:00:00Z','Monthly Subscription');
INSERT INTO "transactions" VALUES('TX04','ACC03','CARD03','Shell Oil',6450,'USD','posted','2026-10-16T17:45:00Z','Gasoline');
INSERT INTO "transactions" VALUES('TX05','ACC04','CARD05','Best Buy',8500,'USD','posted','2026-10-17T14:10:00Z','Electronics');
INSERT INTO "transactions" VALUES('TX06','ACC04','CARD05','CVS Pharmacy',2310,'USD','posted','2026-10-18T11:25:00Z','Pharmacy');
INSERT INTO "transactions" VALUES('TX07','ACC06','CARD07','Olive Garden',3200,'USD','posted','2026-10-19T19:30:00Z','Restaurant dining');
INSERT INTO "transactions" VALUES('TX08','ACC06','CARD07','Zara',4150,'USD','posted','2026-10-20T15:00:00Z','Clothing store');
INSERT INTO "transactions" VALUES('TX09','ACC07','CARD09','Home Depot',15000,'USD','posted','2026-10-20T16:40:00Z','Hardware tools');
INSERT INTO "transactions" VALUES('TX10','ACC07','CARD09','Panera Bread',1890,'USD','posted','2026-10-21T09:05:00Z','Bakery');
INSERT INTO "transactions" VALUES('TX11','ACC09','CARD11','Barnes & Noble',2750,'USD','posted','2026-10-11T13:15:00Z','Book purchase');
INSERT INTO "transactions" VALUES('TX12','ACC09','CARD11','Jiffy Lube',9900,'USD','posted','2026-10-22T10:00:00Z','Auto service');
INSERT INTO "transactions" VALUES('TX13','ACC10','CARD13','Adobe Systems',4999,'USD','posted','2026-10-21T11:30:00Z','Software subscription');
INSERT INTO "transactions" VALUES('TX14','ACC10','CARD13','Petco',3400,'USD','posted','2026-10-22T14:50:00Z','Pet supplies');
INSERT INTO "transactions" VALUES('TX15','ACC12','CARD15','Nordstrom',9200,'USD','posted','2026-10-22T16:10:00Z','Apparel');
INSERT INTO "transactions" VALUES('TX16','ACC12','CARD15','7-Eleven',1100,'USD','posted','2026-10-23T07:45:00Z','Convenience store');
INSERT INTO "transactions" VALUES('TX17','ACC01','CARD01','Target Store #1042',8420,'USD','posted','2026-10-24T14:22:10Z','In-store purchase');
INSERT INTO "transactions" VALUES('TX18','ACC01','CARD01','Corner Cafe',2000,'USD','pending','2026-10-26T09:15:00Z','Pending coffee order');
INSERT INTO "transactions" VALUES('TX19','ACC01','CARD01','Delta Air Lines',12500,'USD','posted','2026-10-18T10:00:00Z','Airline ticket');
INSERT INTO "transactions" VALUES('TX20','ACC01','CARD01','Canteen Vending',350,'USD','posted','2026-10-25T16:00:00Z','Vending machine');
INSERT INTO "transactions" VALUES('TX21','ACC02','CARD02','Amazon.com',5000,'USD','posted','2026-10-10T11:00:00Z','Bookstore');
INSERT INTO "transactions" VALUES('TX22','ACC02','CARD02','Chipotle',1850,'USD','posted','2026-10-12T12:30:00Z','Lunch');
INSERT INTO "transactions" VALUES('TX23','ACC02','CARD02','Trader Joe''s',7200,'USD','posted','2026-10-14T15:20:00Z','Groceries');
INSERT INTO "transactions" VALUES('TX24','ACC02','CARD02','Uber',3100,'USD','pending','2026-10-26T08:00:00Z','Ride share');
INSERT INTO "transactions" VALUES('TX25','ACC03','CARD04','Marriott Hotels',9500,'USD','posted','2026-10-05T14:00:00Z','Hotel stay');
INSERT INTO "transactions" VALUES('TX26','ACC03','CARD04','Cheesecake Factory',4200,'USD','posted','2026-10-15T20:10:00Z','Dinner');
INSERT INTO "transactions" VALUES('TX27','ACC03','CARD04','Walgreens',1500,'USD','posted','2026-10-20T11:45:00Z','Pharmacy');
INSERT INTO "transactions" VALUES('TX28','ACC03','CARD04','McDonald''s',890,'USD','pending','2026-10-26T10:15:00Z','Fast food');
INSERT INTO "transactions" VALUES('TX29','ACC05','CARD06','REI Co-op',6200,'USD','posted','2026-09-25T13:00:00Z','Sporting goods');
INSERT INTO "transactions" VALUES('TX30','ACC05','CARD06','Nike Store',3400,'USD','posted','2026-09-28T16:30:00Z','Shoes');
INSERT INTO "transactions" VALUES('TX31','ACC06','CARD08','United Airlines',11200,'USD','posted','2026-10-08T09:20:00Z','Flight upgrade');
INSERT INTO "transactions" VALUES('TX32','ACC06','CARD08','IKEA',4800,'USD','posted','2026-10-18T14:15:00Z','Furniture');
INSERT INTO "transactions" VALUES('TX33','ACC06','CARD08','P.F. Chang''s',2250,'USD','posted','2026-10-22T19:00:00Z','Dining');
INSERT INTO "transactions" VALUES('TX34','ACC06','CARD08','Chevron',1450,'USD','pending','2026-10-26T07:30:00Z','Gas station');
INSERT INTO "transactions" VALUES('TX35','ACC08','CARD10','Apple Store',21000,'USD','posted','2026-10-02T15:40:00Z','Electronics repair');
INSERT INTO "transactions" VALUES('TX36','ACC08','CARD10','AutoZone',3800,'USD','posted','2026-10-14T11:10:00Z','Auto parts');
INSERT INTO "transactions" VALUES('TX37','ACC08','CARD10','Kroger',5900,'USD','posted','2026-10-21T17:50:00Z','Supermarket');
INSERT INTO "transactions" VALUES('TX38','ACC08','CARD10','Dunkin''',1675,'USD','pending','2026-10-26T08:45:00Z','Coffee');
INSERT INTO "transactions" VALUES('TX39','ACC09','CARD12','Macy''s',14500,'USD','posted','2026-10-04T13:20:00Z','Department store');
INSERT INTO "transactions" VALUES('TX40','ACC09','CARD12','Kay Jewelers',8300,'USD','posted','2026-10-16T16:00:00Z','Jewelry');
INSERT INTO "transactions" VALUES('TX41','ACC09','CARD12','Outback Steakhouse',2950,'USD','posted','2026-10-23T20:30:00Z','Steakhouse');
INSERT INTO "transactions" VALUES('TX42','ACC09','CARD12','Lyft',1200,'USD','pending','2026-10-26T06:50:00Z','Ride share');
INSERT INTO "transactions" VALUES('TX43','ACC11','CARD14','Lowes',7800,'USD','posted','2026-10-07T10:15:00Z','Garden nursery');
INSERT INTO "transactions" VALUES('TX44','ACC11','CARD14','Staples',4600,'USD','posted','2026-10-19T14:40:00Z','Office supplies');
INSERT INTO "transactions" VALUES('TX45','ACC11','CARD14','Local Bakery',1980,'USD','posted','2026-10-24T09:30:00Z','Bakery');
INSERT INTO "transactions" VALUES('TX46','ACC11','CARD14','Mister Car Wash',3500,'USD','pending','2026-10-26T11:00:00Z','Car wash');
INSERT INTO "transactions" VALUES('TX47','ACC12','CARD16','Saks Fifth Avenue',18900,'USD','posted','2026-10-01T15:00:00Z','Designer store');
INSERT INTO "transactions" VALUES('TX48','ACC12','CARD16','Sephora',6700,'USD','posted','2026-10-17T12:45:00Z','Cosmetics');
INSERT INTO "transactions" VALUES('TX49','ACC12','CARD16','Buca di Beppo',3100,'USD','posted','2026-10-23T18:15:00Z','Italian bistro');
INSERT INTO "transactions" VALUES('TX50','ACC12','CARD16','Circle K',950,'USD','pending','2026-10-26T07:10:00Z','Convenience store');
INSERT INTO "transactions" VALUES('TX51','ACC13','CARD17','Ace Hardware',5400,'USD','posted','2026-10-06T11:30:00Z','Hardware store');
INSERT INTO "transactions" VALUES('TX52','ACC13','CARD17','Domino''s Pizza',3900,'USD','posted','2026-10-18T20:00:00Z','Pizza restaurant');
INSERT INTO "transactions" VALUES('TX53','ACC13','CARD17','Publix',2150,'USD','posted','2026-10-24T16:20:00Z','Supermarket');
INSERT INTO "transactions" VALUES('TX54','ACC13','CARD17','EZ Pass',800,'USD','pending','2026-10-26T08:20:00Z','Toll charge');
INSERT INTO "transactions" VALUES('TX55','ACC14','CARD18','B&H Photo',13500,'USD','posted','2026-10-03T14:10:00Z','Camera store');
INSERT INTO "transactions" VALUES('TX56','ACC14','CARD18','Warby Parker',7100,'USD','posted','2026-10-15T13:00:00Z','Eyewear');
INSERT INTO "transactions" VALUES('TX57','ACC14','CARD18','Red Lobster',4400,'USD','posted','2026-10-22T19:40:00Z','Dining');
INSERT INTO "transactions" VALUES('TX58','ACC14','CARD18','Peet''s Coffee',1150,'USD','pending','2026-10-26T09:40:00Z','Coffee');
INSERT INTO "transactions" VALUES('TX59','ACC15','CARD19','Costco Wholesale',8800,'USD','posted','2026-10-10T15:30:00Z','Wholesale club');
INSERT INTO "transactions" VALUES('TX60','ACC15','CARD19','VCA Animal Hospital',3200,'USD','posted','2026-10-21T10:00:00Z','Pet hospital');
INSERT INTO "transactions" VALUES('TX61','ACC15','CARD19','Corner Bakery',2650,'USD','posted','2026-10-24T08:15:00Z','Bakery');
INSERT INTO "transactions" VALUES('TX62','ACC15','CARD19','City Parking',1500,'USD','pending','2026-10-26T10:00:00Z','Parking meter');
INSERT INTO "transactions" VALUES('TX63','ACC15','CARD20','Tiffany & Co.',16200,'USD','posted','2026-10-05T12:00:00Z','Jeweler');
INSERT INTO "transactions" VALUES('TX64','ACC15','CARD20','Lululemon',5400,'USD','posted','2026-10-16T14:30:00Z','Apparel');
INSERT INTO "transactions" VALUES('TX65','ACC15','CARD20','Bistro Le Marais',3800,'USD','posted','2026-10-23T20:00:00Z','French restaurant');
INSERT INTO "transactions" VALUES('TX66','ACC15','CARD20','City Flowers',2200,'USD','pending','2026-10-26T09:00:00Z','Florist');
INSERT INTO "transactions" VALUES('TX67','ACC16','CARD21','Dick''s Sporting Goods',6100,'USD','posted','2026-10-08T16:20:00Z','Sporting goods');
INSERT INTO "transactions" VALUES('TX68','ACC16','CARD21','Books-A-Million',2900,'USD','posted','2026-10-19T11:10:00Z','Bookstore');
INSERT INTO "transactions" VALUES('TX69','ACC16','CARD21','Jamba Juice',1750,'USD','posted','2026-10-25T13:45:00Z','Smoothie shop');
INSERT INTO "transactions" VALUES('TX70','ACC16','CARD21','Hudson News',450,'USD','pending','2026-10-26T06:30:00Z','Newspaper stand');
INSERT INTO "transactions" VALUES('TX71','ACC17','CARD22','West Elm',9400,'USD','posted','2026-09-12T14:00:00Z','Furniture store');
INSERT INTO "transactions" VALUES('TX72','ACC17','CARD22','Williams-Sonoma',4100,'USD','posted','2026-09-14T15:30:00Z','Kitchenware');
INSERT INTO "transactions" VALUES('TX73','ACC18','CARD23','Safeway',7300,'USD','posted','2026-10-09T10:15:00Z','Supermarket');
INSERT INTO "transactions" VALUES('TX74','ACC18','CARD23','Rite Aid',3600,'USD','posted','2026-10-20T17:00:00Z','Pharmacy');
INSERT INTO "transactions" VALUES('TX75','ACC18','CARD23','Five Guys',2450,'USD','posted','2026-10-24T12:40:00Z','Burger joint');
INSERT INTO "transactions" VALUES('TX76','ACC18','CARD23','Auto Spa',1300,'USD','pending','2026-10-26T10:45:00Z','Car wash');
INSERT INTO "transactions" VALUES('TX77','ACC18','CARD24','Bose Store',17800,'USD','posted','2026-10-04T16:00:00Z','Electronics store');
INSERT INTO "transactions" VALUES('TX78','ACC18','CARD24','Banana Republic',6200,'USD','posted','2026-10-17T11:50:00Z','Clothing store');
INSERT INTO "transactions" VALUES('TX79','ACC18','CARD24','Legal Sea Foods',4900,'USD','posted','2026-10-22T19:15:00Z','Seafood restaurant');
INSERT INTO "transactions" VALUES('TX80','ACC18','CARD24','Au Bon Pain',850,'USD','pending','2026-10-26T08:10:00Z','Bakery');
INSERT INTO "transactions" VALUES('TX81','ACC01','CARD01','ExxonMobil',3250,'USD','posted','2026-10-12T16:00:00Z','Gasoline');
INSERT INTO "transactions" VALUES('TX82','ACC01','CARD01','Spotify',1199,'USD','posted','2026-10-13T00:00:00Z','Music streaming');
INSERT INTO "transactions" VALUES('TX83','ACC01','CARD01','Kohl''s',6700,'USD','posted','2026-10-21T18:20:00Z','Department store');
INSERT INTO "transactions" VALUES('TX84','ACC01','CARD01','Wendy''s',1450,'USD','posted','2026-10-23T12:10:00Z','Fast food');
INSERT INTO "transactions" VALUES('TX85','ACC01','CARD01','Metro Transit',950,'USD','posted','2026-10-25T08:00:00Z','Transit ticket');
INSERT INTO "transactions" VALUES('TX86','ACC03','CARD03','Gap',5100,'USD','posted','2026-10-11T14:00:00Z','Clothing');
INSERT INTO "transactions" VALUES('TX87','ACC03','CARD03','AMC Theatres',2200,'USD','posted','2026-10-18T20:15:00Z','Movie tickets');
INSERT INTO "transactions" VALUES('TX88','ACC04','CARD05','Foot Locker',4800,'USD','posted','2026-10-12T15:30:00Z','Shoes');
INSERT INTO "transactions" VALUES('TX89','ACC04','CARD05','Panera Bread',1650,'USD','posted','2026-10-22T13:00:00Z','Lunch');
INSERT INTO "transactions" VALUES('TX90','ACC06','CARD07','Sprouts Farmers Market',7900,'USD','posted','2026-10-14T11:40:00Z','Groceries');
INSERT INTO "transactions" VALUES('TX91','ACC06','CARD07','Target',2800,'USD','posted','2026-10-21T17:15:00Z','Home decor');
INSERT INTO "transactions" VALUES('TX92','ACC07','CARD09','Big 5 Sporting Goods',6300,'USD','posted','2026-10-13T10:50:00Z','Sporting goods');
INSERT INTO "transactions" VALUES('TX93','ACC07','CARD09','Starbucks',1400,'USD','posted','2026-10-22T08:30:00Z','Coffee');
INSERT INTO "transactions" VALUES('TX94','ACC09','CARD11','Target',8700,'USD','posted','2026-10-15T16:45:00Z','Department store');
INSERT INTO "transactions" VALUES('TX95','ACC09','CARD11','Panda Express',3100,'USD','posted','2026-10-24T19:10:00Z','Dining');
INSERT INTO "transactions" VALUES('TX96','ACC10','CARD13','Old Navy',5200,'USD','posted','2026-10-16T13:20:00Z','Apparel');
INSERT INTO "transactions" VALUES('TX97','ACC10','CARD13','Walgreens',1950,'USD','posted','2026-10-23T11:00:00Z','Pharmacy');
INSERT INTO "transactions" VALUES('TX98','ACC12','CARD15','Bed Bath & Beyond',7400,'USD','posted','2026-10-14T14:30:00Z','Home goods');
INSERT INTO "transactions" VALUES('TX99','ACC12','CARD15','Shake Shack',2300,'USD','posted','2026-10-24T18:00:00Z','Dining');
INSERT INTO "transactions" VALUES('TX100','ACC13','CARD17','Home Depot',4100,'USD','posted','2026-10-20T12:00:00Z','Hardware');
