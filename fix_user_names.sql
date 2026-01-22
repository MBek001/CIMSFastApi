-- Fix user names in database
-- Current issue: name and surname columns have literal "string" values

-- Check current user data
SELECT id, name, surname, telegram_id FROM "user" WHERE telegram_id IN ('mirzohid', 'ahmad');

-- Fix user names based on telegram_id
UPDATE "user" SET
    name = 'Mirzohid',
    surname = 'Bekmurodov'
WHERE telegram_id = 'mirzohid';

UPDATE "user" SET
    name = 'Ahmad',
    surname = 'Ahmadov'
WHERE telegram_id = 'ahmad';

-- Verify the fix
SELECT id, name, surname, telegram_id FROM "user" WHERE telegram_id IN ('mirzohid', 'ahmad');
