-- Migration: Add Customer Type and International Sales Page
-- Date: 2025-12-15
-- Description: Adds customer type column and international_sales page permission

-- ========================================
-- 1. Add customer type column to customer table
-- ========================================

-- Create CustomerType enum if not exists
DO $$ BEGIN
    CREATE TYPE customertype AS ENUM ('default', 'international');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add type column to customer table
ALTER TABLE customer
ADD COLUMN IF NOT EXISTS type customertype DEFAULT NULL;

-- Create index for customer type
CREATE INDEX IF NOT EXISTS idx_customer_type ON customer(type);

-- ========================================
-- 2. Add international_sales to PageName enum
-- ========================================

-- PostgreSQL doesn't allow adding enum values in a transaction-safe way directly
-- We need to add it using ALTER TYPE
DO $$ BEGIN
    -- Check if international_sales already exists
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'international_sales'
        AND enumtypid = (
            SELECT oid FROM pg_type WHERE typname = 'pagename'
        )
    ) THEN
        -- Add new enum value
        ALTER TYPE pagename ADD VALUE 'international_sales';
    END IF;
END $$;

-- ========================================
-- NOTES:
-- ========================================
-- This migration adds:
-- 1. customer.type column (default/international) - nullable, default NULL
-- 2. international_sales page to PageName enum
--
-- After running this migration:
-- 1. Existing customers will have type=NULL (treated as default)
-- 2. New customers can specify type="international" or "default"
-- 3. CEO can grant international_sales page permission to users
-- 4. Use /sales/stats and /sales/detailed endpoints for statistics
-- 5. Use /sales/international endpoint for international leads list
--
-- API Usage:
-- POST /crm/customers with customer_type="international" or "default"
-- GET /sales/stats?customer_type=international
-- GET /sales/detailed?days=30&customer_type=international
-- GET /sales/international
