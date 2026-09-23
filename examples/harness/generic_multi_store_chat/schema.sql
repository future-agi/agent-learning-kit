CREATE TABLE IF NOT EXISTS accounts (
    account_id text PRIMARY KEY,
    support_status text NOT NULL
);

INSERT INTO accounts (account_id, support_status)
VALUES ('ACC-2048', 'active')
ON CONFLICT (account_id) DO UPDATE SET support_status = EXCLUDED.support_status;
