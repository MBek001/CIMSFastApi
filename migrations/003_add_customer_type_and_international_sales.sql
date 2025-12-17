-- Migration: Add Customer Type (local/international)
-- Date: 2025-12-16
-- Description: Adds customer type column (local/international, null allowed)

-- ========================================
-- 1. Add customer type column to customer table
-- ========================================

-- Create CustomerType enum if not exists
DO $$ BEGIN
    CREATE TYPE customertype AS ENUM ('local', 'international');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add type column to customer table
ALTER TABLE customer
ADD COLUMN IF NOT EXISTS type customertype DEFAULT NULL;

-- Create index for customer type
CREATE INDEX IF NOT EXISTS idx_customer_type ON customer(type);

-- ========================================
-- NOTES:
-- ========================================
-- This migration adds:
-- 1. customer.type column (local/international) - nullable, default NULL
--
-- After running this migration:
-- 1. Existing customers will have type=NULL (treated as local)
-- 2. New customers can specify type="international" or "local"
-- 3. All leads are visible in CRM page - filter by type to see local/international
-- 4. Use /sales/stats and /sales/detailed endpoints for statistics with type filtering
--
-- API Usage:
-- POST /crm/customers with customer_type="international" or "local"
-- GET /sales/stats?customer_type=local (local leads including null)
-- GET /sales/stats?customer_type=international (only international leads)
-- GET /sales/stats (all leads - no filter)
-- GET /sales/detailed?days=30&customer_type=local
-- GET /sales/international (only international leads list)
