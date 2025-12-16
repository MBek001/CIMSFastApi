-- Verify the customer type column exists
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'customer' AND column_name = 'type';

-- Verify customertype enum exists
SELECT enumlabel
FROM pg_enum
WHERE enumtypid = (SELECT oid FROM pg_type WHERE typname = 'customertype')
ORDER BY enumsortorder;

-- Verify international_sales in PageName enum
SELECT enumlabel
FROM pg_enum
WHERE enumtypid = (SELECT oid FROM pg_type WHERE typname = 'pagename')
ORDER BY enumsortorder;
