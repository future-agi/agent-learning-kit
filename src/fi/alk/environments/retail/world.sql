-- retail_returns_desk's world: schema and seed, as the environment builder sealed it for phase-1
-- sheet task V15. Hosted runs seed PostgreSQL from this world.

CREATE TABLE clock (
    now TEXT PRIMARY KEY
);
INSERT INTO "clock" VALUES('2026-10-26T12:00:00Z');
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    postal_code TEXT NOT NULL
);
INSERT INTO "customers" VALUES('C01','Alice Morgan','alice.morgan@example.com','+15550101','90210');
INSERT INTO "customers" VALUES('C02','Bob Smith','bob.smith@example.com','+15550102','10001');
INSERT INTO "customers" VALUES('C03','Carol Danvers','carol.danvers@example.com','+15550103','60601');
INSERT INTO "customers" VALUES('C04','David Chen','david.chen@example.com','+15550104','94102');
INSERT INTO "customers" VALUES('C05','Elena Rodriguez','elena.rodriguez@example.com','+15550105','33101');
INSERT INTO "customers" VALUES('C06','Frank Miller','frank.miller@example.com','+15550106','98101');
INSERT INTO "customers" VALUES('C07','Grace Hopper','grace.hopper@example.com','+15550107','02138');
INSERT INTO "customers" VALUES('C08','Henry Ford','henry.ford@example.com','+15550108','48201');
INSERT INTO "customers" VALUES('C09','Iris West','iris.west@example.com','+15550109','63101');
INSERT INTO "customers" VALUES('C10','Jack Ryan','jack.ryan@example.com','+15550110','20001');
INSERT INTO "customers" VALUES('C11','Karen Page','karen.page@example.com','+15550111','10003');
INSERT INTO "customers" VALUES('C12','Leo Fitz','leo.fitz@example.com','+15550112','75001');
INSERT INTO "customers" VALUES('C13','Mia Wong','mia.wong@example.com','+15550113','97201');
INSERT INTO "customers" VALUES('C14','Noah Taylor','noah.taylor@example.com','+15550114','80202');
INSERT INTO "customers" VALUES('C15','Olivia Pope','olivia.pope@example.com','+15550115','20006');
INSERT INTO "customers" VALUES('C16','Peter Parker','peter.parker@example.com','+15550116','11375');
INSERT INTO "customers" VALUES('C17','Quinn Fabray','quinn.fabray@example.com','+15550117','43215');
INSERT INTO "customers" VALUES('C18','Rachel Green','rachel.green@example.com','+15550118','10014');
INSERT INTO "customers" VALUES('C19','Steve Rogers','steve.rogers@example.com','+15550119','11201');
INSERT INTO "customers" VALUES('C20','Tara Maclay','tara.maclay@example.com','+15550120','94704');
CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "environment_policy" VALUES('return_window_days','30');
INSERT INTO "environment_policy" VALUES('restocking_fee_rate','0');
INSERT INTO "environment_policy" VALUES('final_sale_enforced','true');
INSERT INTO "environment_policy" VALUES('label_service_available','true');
CREATE TABLE order_items (
    item_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    line_discount_cents INTEGER NOT NULL,
    line_tax_cents INTEGER NOT NULL,
    line_net_cents INTEGER NOT NULL,
    returned_quantity INTEGER NOT NULL
);
INSERT INTO "order_items" VALUES('IT01','O01','SH01',2,8000,0,0,16000,0);
INSERT INTO "order_items" VALUES('IT02','O02','SH02',1,9500,0,0,9500,0);
INSERT INTO "order_items" VALUES('IT03','O03','FS01',1,12000,0,0,12000,0);
INSERT INTO "order_items" VALUES('IT04','O04','AP14',1,4000,400,0,3600,0);
INSERT INTO "order_items" VALUES('IT05','O05','AP05',1,7800,0,0,7800,0);
INSERT INTO "order_items" VALUES('IT06','O06','AP06',1,6800,0,0,6800,0);
INSERT INTO "order_items" VALUES('IT07','O07','SH04',1,11000,0,0,11000,0);
INSERT INTO "order_items" VALUES('IT08','O08','AP09',1,8800,500,0,8300,0);
INSERT INTO "order_items" VALUES('IT09','O09','AP12',1,14500,0,0,14500,0);
INSERT INTO "order_items" VALUES('IT10','O10','AP11',1,9200,1000,0,8200,0);
INSERT INTO "order_items" VALUES('IT11','O11','AC07',1,8500,0,0,8500,0);
INSERT INTO "order_items" VALUES('IT12','O12','AC08',1,7500,0,0,7500,0);
INSERT INTO "order_items" VALUES('IT13','O13','SH03',1,12000,0,0,12000,0);
INSERT INTO "order_items" VALUES('IT14','O14','AP08',1,5200,0,0,5200,0);
INSERT INTO "order_items" VALUES('IT15','O15','SH06',1,6500,0,0,6500,0);
INSERT INTO "order_items" VALUES('IT16','O16','AP10',1,11000,0,0,11000,0);
INSERT INTO "order_items" VALUES('IT17','O17','AP07',1,4800,0,0,4800,0);
INSERT INTO "order_items" VALUES('IT18','O18','AP03',1,5800,800,0,5000,0);
INSERT INTO "order_items" VALUES('IT19','O19','SH05',1,4500,0,0,4500,0);
INSERT INTO "order_items" VALUES('IT20','O20','SH10',1,13500,0,0,13500,0);
INSERT INTO "order_items" VALUES('IT21','O21','AP02',1,3400,0,0,3400,0);
INSERT INTO "order_items" VALUES('IT22','O22','AP04',1,6400,0,0,6400,0);
INSERT INTO "order_items" VALUES('IT23','O23','AP13',1,4200,0,0,4200,0);
INSERT INTO "order_items" VALUES('IT24','O24','AP01',1,2800,0,0,2800,0);
INSERT INTO "order_items" VALUES('IT25','O25','SH09',1,5500,0,0,5500,0);
INSERT INTO "order_items" VALUES('IT26','O26','SH07',1,7500,0,0,7500,0);
INSERT INTO "order_items" VALUES('IT27','O27','SH08',1,3500,0,0,3500,0);
INSERT INTO "order_items" VALUES('IT28','O28','AC05',1,4200,0,0,4200,0);
INSERT INTO "order_items" VALUES('IT29','O29','AC06',1,2800,0,0,2800,0);
INSERT INTO "order_items" VALUES('IT30','O30','AC09',1,4800,0,0,4800,0);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    order_date TEXT NOT NULL,
    delivery_date TEXT,
    status TEXT NOT NULL,
    currency TEXT NOT NULL,
    total_gross_cents INTEGER NOT NULL,
    total_discount_cents INTEGER NOT NULL,
    total_tax_cents INTEGER NOT NULL,
    total_net_cents INTEGER NOT NULL
);
INSERT INTO "orders" VALUES('O01','C01','2026-10-16T14:30:00Z','2026-10-20T10:00:00Z','delivered','USD',16000,0,0,16000);
INSERT INTO "orders" VALUES('O02','C02','2026-10-14T09:15:00Z','2026-10-18T11:00:00Z','delivered','USD',9500,0,0,9500);
INSERT INTO "orders" VALUES('O03','C03','2026-10-17T11:00:00Z','2026-10-21T15:20:00Z','delivered','USD',12000,0,0,12000);
INSERT INTO "orders" VALUES('O04','C04','2026-10-15T16:45:00Z','2026-10-19T14:00:00Z','delivered','USD',4000,400,0,3600);
INSERT INTO "orders" VALUES('O05','C05','2026-10-10T10:00:00Z','2026-10-15T12:00:00Z','delivered','USD',7800,0,0,7800);
INSERT INTO "orders" VALUES('O06','C06','2026-08-10T08:00:00Z','2026-08-15T10:00:00Z','delivered','USD',6800,0,0,6800);
INSERT INTO "orders" VALUES('O07','C07','2026-10-24T18:00:00Z',NULL,'in_transit','USD',11000,0,0,11000);
INSERT INTO "orders" VALUES('O08','C08','2026-10-18T13:20:00Z','2026-10-22T09:30:00Z','delivered','USD',8800,500,0,8300);
INSERT INTO "orders" VALUES('O09','C09','2026-10-19T10:00:00Z','2026-10-23T16:10:00Z','delivered','USD',14500,0,0,14500);
INSERT INTO "orders" VALUES('O10','C10','2026-10-08T15:30:00Z','2026-10-12T11:45:00Z','delivered','USD',9200,1000,0,8200);
INSERT INTO "orders" VALUES('O11','C11','2026-10-17T09:00:00Z','2026-10-21T13:00:00Z','delivered','USD',8500,0,0,8500);
INSERT INTO "orders" VALUES('O12','C12','2026-10-16T12:00:00Z','2026-10-20T17:00:00Z','delivered','USD',7500,0,0,7500);
INSERT INTO "orders" VALUES('O13','C13','2026-08-27T14:00:00Z','2026-09-01T10:00:00Z','delivered','USD',12000,0,0,12000);
INSERT INTO "orders" VALUES('O14','C14','2026-10-25T11:00:00Z',NULL,'processing','USD',5200,0,0,5200);
INSERT INTO "orders" VALUES('O15','C15','2026-10-13T10:00:00Z','2026-10-17T15:00:00Z','delivered','USD',6500,0,0,6500);
INSERT INTO "orders" VALUES('O16','C16','2026-10-18T16:00:00Z','2026-10-22T14:30:00Z','delivered','USD',11000,0,0,11000);
INSERT INTO "orders" VALUES('O17','C17','2026-10-15T11:20:00Z','2026-10-19T10:30:00Z','delivered','USD',4800,0,0,4800);
INSERT INTO "orders" VALUES('O18','C18','2026-10-10T14:00:00Z','2026-10-14T12:00:00Z','delivered','USD',5800,800,0,5000);
INSERT INTO "orders" VALUES('O19','C19','2026-10-20T10:15:00Z','2026-10-24T16:00:00Z','delivered','USD',4500,0,0,4500);
INSERT INTO "orders" VALUES('O20','C20','2026-10-12T09:45:00Z','2026-10-16T11:00:00Z','delivered','USD',13500,0,0,13500);
INSERT INTO "orders" VALUES('O21','C01','2026-10-19T15:00:00Z','2026-10-23T10:00:00Z','delivered','USD',3400,0,0,3400);
INSERT INTO "orders" VALUES('O22','C02','2026-10-17T13:00:00Z','2026-10-21T12:00:00Z','delivered','USD',6400,0,0,6400);
INSERT INTO "orders" VALUES('O23','C03','2026-10-21T10:00:00Z','2026-10-25T14:00:00Z','delivered','USD',4200,0,0,4200);
INSERT INTO "orders" VALUES('O24','C04','2026-10-14T11:30:00Z','2026-10-18T16:00:00Z','delivered','USD',2800,0,0,2800);
INSERT INTO "orders" VALUES('O25','C05','2026-10-18T14:40:00Z','2026-10-22T11:00:00Z','delivered','USD',5500,0,0,5500);
INSERT INTO "orders" VALUES('O26','C06','2026-10-16T10:10:00Z','2026-10-20T15:00:00Z','delivered','USD',7500,0,0,7500);
INSERT INTO "orders" VALUES('O27','C07','2026-10-20T16:00:00Z','2026-10-24T12:00:00Z','delivered','USD',3500,0,0,3500);
INSERT INTO "orders" VALUES('O28','C08','2026-10-15T09:00:00Z','2026-10-19T13:00:00Z','delivered','USD',4200,0,0,4200);
INSERT INTO "orders" VALUES('O29','C09','2026-10-17T11:00:00Z','2026-10-21T10:00:00Z','delivered','USD',2800,0,0,2800);
INSERT INTO "orders" VALUES('O30','C10','2026-10-21T15:00:00Z','2026-10-25T16:00:00Z','delivered','USD',4800,0,0,4800);
CREATE TABLE products (
    sku TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    list_price_cents INTEGER NOT NULL,
    is_final_sale BOOLEAN NOT NULL,
    returnable BOOLEAN NOT NULL
);
INSERT INTO "products" VALUES('SH01','Road Running Shoes','Footwear',8000,0,1);
INSERT INTO "products" VALUES('SH02','Trail Running Sneakers','Footwear',9500,0,1);
INSERT INTO "products" VALUES('SH03','Leather Oxford Shoes','Footwear',12000,0,1);
INSERT INTO "products" VALUES('SH04','Classic Chelsea Boots','Footwear',11000,0,1);
INSERT INTO "products" VALUES('SH05','Canvas Low-Top Sneakers','Footwear',4500,0,1);
INSERT INTO "products" VALUES('SH06','Suede Casual Loafers','Footwear',6500,0,1);
INSERT INTO "products" VALUES('SH07','Waterproof Hiking Boots','Footwear',7500,0,1);
INSERT INTO "products" VALUES('SH08','Comfort Slide Sandals','Footwear',3500,0,1);
INSERT INTO "products" VALUES('SH09','Slip-On Walking Shoes','Footwear',5500,0,1);
INSERT INTO "products" VALUES('SH10','Premium Dress Brogues','Footwear',13500,0,1);
INSERT INTO "products" VALUES('FS01','Clearance Leather Moto Jacket','Clearance',12000,1,0);
INSERT INTO "products" VALUES('FS02','Final Sale Graphic Hoodie','Clearance',4000,1,0);
INSERT INTO "products" VALUES('FS03','Final Sale Denim Shorts','Clearance',3000,1,0);
INSERT INTO "products" VALUES('FS04','Archive Silk Bandana','Clearance',2500,1,0);
INSERT INTO "products" VALUES('FS05','Clearance Wool Scarf','Clearance',6000,1,0);
INSERT INTO "products" VALUES('FS06','Sample Sale Cardigan','Clearance',4500,1,0);
INSERT INTO "products" VALUES('FS07','Warehouse Sale Trench Coat','Clearance',7000,1,0);
INSERT INTO "products" VALUES('FS08','Final Mark Beanie','Clearance',2000,1,0);
INSERT INTO "products" VALUES('FS09','Clearance Linen Top','Clearance',3500,1,0);
INSERT INTO "products" VALUES('FS10','Final Sale Swim Trunks','Clearance',5000,1,0);
INSERT INTO "products" VALUES('AP01','Organic Cotton Crewneck T-Shirt','Apparel',2800,0,1);
INSERT INTO "products" VALUES('AP02','Pima Cotton V-Neck T-Shirt','Apparel',3400,0,1);
INSERT INTO "products" VALUES('AP03','Slim-Fit Oxford Button-Down','Apparel',5800,0,1);
INSERT INTO "products" VALUES('AP04','Linen Long-Sleeve Shirt','Apparel',6400,0,1);
INSERT INTO "products" VALUES('AP05','Stretch Selvedge Denim Jeans','Apparel',7800,0,1);
INSERT INTO "products" VALUES('AP06','Relaxed Fit Chino Trousers','Apparel',6800,0,1);
INSERT INTO "products" VALUES('AP07','Everyday French Terry Sweatshirt','Apparel',4800,0,1);
INSERT INTO "products" VALUES('AP08','Pullover Fleece Hoodie','Apparel',5200,0,1);
INSERT INTO "products" VALUES('AP09','Merino Wool Crewneck Sweater','Apparel',8800,0,1);
INSERT INTO "products" VALUES('AP10','Cashmere Turtleneck Sweater','Apparel',11000,0,1);
INSERT INTO "products" VALUES('AP11','Water-Resistant Windbreaker Jacket','Apparel',9200,0,1);
INSERT INTO "products" VALUES('AP12','Insulated Winter Parka','Apparel',14500,0,1);
INSERT INTO "products" VALUES('AP13','Tailored Boardshorts','Apparel',4200,0,1);
INSERT INTO "products" VALUES('AP14','Classic Polo Shirt','Apparel',4000,0,1);
INSERT INTO "products" VALUES('AP15','Athletic Performance Shorts','Apparel',3600,0,1);
INSERT INTO "products" VALUES('AC01','Full-Grain Leather Belt','Accessories',4500,0,1);
INSERT INTO "products" VALUES('AC02','Woven Canvas D-Ring Belt','Accessories',3800,0,1);
INSERT INTO "products" VALUES('AC03','Polarized Aviator Sunglasses','Accessories',6500,0,1);
INSERT INTO "products" VALUES('AC04','Classic Wayfarer Sunglasses','Accessories',5500,0,1);
INSERT INTO "products" VALUES('AC05','Bifold Leather Wallet','Accessories',4200,0,1);
INSERT INTO "products" VALUES('AC06','Slim RFID Cardholder','Accessories',2800,0,1);
INSERT INTO "products" VALUES('AC07','Water-Repellent Commuter Backpack','Accessories',8500,0,1);
INSERT INTO "products" VALUES('AC08','Waxed Canvas Messenger Bag','Accessories',7500,0,1);
INSERT INTO "products" VALUES('AC09','Weekend Canvas Duffle Bag','Accessories',4800,0,1);
INSERT INTO "products" VALUES('AC10','Ribbed Merino Beanie','Accessories',1800,0,1);
INSERT INTO "products" VALUES('AC11','Soft Cashmere Touch Scarf','Accessories',3200,0,1);
INSERT INTO "products" VALUES('AC12','Wool Blend Touchscreen Gloves','Accessories',2200,0,1);
INSERT INTO "products" VALUES('AC13','Cushioned Athletic Socks 3-Pack','Accessories',1500,0,1);
INSERT INTO "products" VALUES('AC14','Adjustable Twill Baseball Cap','Accessories',2400,0,1);
INSERT INTO "products" VALUES('AC15','Stainless Steel Water Bottle 24oz','Accessories',3000,0,1);
CREATE TABLE return_items (
    return_item_id TEXT PRIMARY KEY,
    return_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    refunded_amount_cents INTEGER NOT NULL
);
CREATE TABLE return_labels (
    label_id TEXT PRIMARY KEY,
    return_id TEXT NOT NULL,
    tracking_number TEXT NOT NULL,
    carrier TEXT NOT NULL,
    label_url TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE returns (
    return_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    estimated_refund_cents INTEGER NOT NULL,
    restocking_fee_cents INTEGER NOT NULL,
    return_reason TEXT NOT NULL
);
CREATE TABLE support_cases (
    case_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    order_id TEXT,
    subject TEXT NOT NULL,
    details TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
