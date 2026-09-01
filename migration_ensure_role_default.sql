-- Safe, idempotent migration for requirement #9.
-- Run this any time — it only affects rows where role is genuinely NULL.
-- (Your schema.sql already has "NOT NULL DEFAULT 'customer'" on this
-- column for new rows, so this is only needed if you have old data
-- imported from elsewhere that might have skipped that default.)

UPDATE users SET role = 'customer' WHERE role IS NULL;