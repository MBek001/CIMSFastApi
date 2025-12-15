-- Migration: Add Dynamic Status and Role Management Tables
-- Date: 2025-12-15
-- Description: Adds customer_status, user_role, sales_manager_assignment, and sales_manager_counter tables

-- ========================================
-- 1. Customer Status Table (Dynamic)
-- ========================================
CREATE TABLE IF NOT EXISTS customer_status (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    description TEXT,
    color VARCHAR(50),
    "order" INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    is_system BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert default statuses
INSERT INTO customer_status (name, display_name, description, color, "order", is_system) VALUES
    ('contacted', 'Contacted', 'Initial contact made', '#3B82F6', 1, TRUE),
    ('project_started', 'Project Started', 'Project has started', '#10B981', 2, TRUE),
    ('continuing', 'Continuing', 'Project is continuing', '#F59E0B', 3, TRUE),
    ('finished', 'Finished', 'Project completed', '#8B5CF6', 4, TRUE),
    ('rejected', 'Rejected', 'Lead rejected', '#EF4444', 5, TRUE),
    ('need_to_call', 'Need to Call', 'Follow-up call needed', '#F97316', 6, TRUE)
ON CONFLICT (name) DO NOTHING;

-- ========================================
-- 2. User Role Table (Dynamic)
-- ========================================
CREATE TABLE IF NOT EXISTS user_role (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    is_system BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert default roles
INSERT INTO user_role (name, display_name, description, is_system) VALUES
    ('ceo', 'CEO', 'Chief Executive Officer', TRUE),
    ('financial_director', 'Financial Director', 'Financial Director', TRUE),
    ('sales_manager', 'Sales Manager', 'Sales Manager for CRM', TRUE),
    ('member', 'Member', 'Team Member', TRUE),
    ('customer', 'Customer', 'Customer/Client', TRUE)
ON CONFLICT (name) DO NOTHING;

-- ========================================
-- 3. Add new columns to existing tables
-- ========================================

-- Add status_name to customer table (for dynamic statuses)
ALTER TABLE customer
ADD COLUMN IF NOT EXISTS status_name VARCHAR(100);

-- Add role_name to user table (for dynamic roles)
ALTER TABLE "user"
ADD COLUMN IF NOT EXISTS role_name VARCHAR(100);

-- ========================================
-- 4. Sales Manager Assignment Table
-- ========================================
CREATE TABLE IF NOT EXISTS sales_manager_assignment (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customer(id) ON DELETE CASCADE,
    sales_manager_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    assigned_at TIMESTAMP DEFAULT NOW(),
    assigned_by INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT TRUE,
    CONSTRAINT uq_customer_assignment UNIQUE (customer_id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_sm_assignment_customer ON sales_manager_assignment(customer_id);
CREATE INDEX IF NOT EXISTS idx_sm_assignment_manager ON sales_manager_assignment(sales_manager_id);
CREATE INDEX IF NOT EXISTS idx_sm_assignment_active ON sales_manager_assignment(is_active);

-- ========================================
-- 5. Sales Manager Counter Table (Round-robin tracking)
-- ========================================
CREATE TABLE IF NOT EXISTS sales_manager_counter (
    id SERIAL PRIMARY KEY,
    last_assigned_index INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Initialize counter with one row
INSERT INTO sales_manager_counter (last_assigned_index) VALUES (0)
ON CONFLICT DO NOTHING;

-- ========================================
-- 6. Create indexes for new tables
-- ========================================
CREATE INDEX IF NOT EXISTS idx_customer_status_name ON customer_status(name);
CREATE INDEX IF NOT EXISTS idx_customer_status_active ON customer_status(is_active);
CREATE INDEX IF NOT EXISTS idx_user_role_name ON user_role(name);
CREATE INDEX IF NOT EXISTS idx_user_role_active ON user_role(is_active);

-- ========================================
-- NOTES:
-- ========================================
-- After running this migration, you should:
-- 1. Migrate existing customer.status enum values to customer.status_name
-- 2. Migrate existing user.role enum values to user.role_name
-- 3. Test all CRM and user management endpoints
--
-- Migration commands:
-- UPDATE customer SET status_name = status::text WHERE status_name IS NULL;
-- UPDATE "user" SET role_name = role::text WHERE role_name IS NULL;
