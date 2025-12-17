-- Migration: Add Update Tracking System with Telegram Integration
-- Date: 2025-12-17
-- Description: Adds tables for automatic update tracking from Telegram channel

-- ========================================
-- 1. Department table
-- ========================================
CREATE TABLE IF NOT EXISTS department (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    description TEXT,
    expected_updates_per_week INTEGER DEFAULT 5,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert default departments
INSERT INTO department (name, display_name, description, expected_updates_per_week) VALUES
('dev_team', 'Development Team', 'Software development and technical team', 6),
('marketing_team', 'Marketing Team', 'Marketing and content creation team', 6),
('local_sales', 'Local Sales', 'Local sales and customer relations', 6),
('international_sales', 'International Sales', 'International sales and business development', 6)
ON CONFLICT (name) DO NOTHING;

-- ========================================
-- 2. User Department mapping table
-- ========================================
CREATE TABLE IF NOT EXISTS user_department (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    department_id INTEGER NOT NULL REFERENCES department(id) ON DELETE CASCADE,
    joined_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    CONSTRAINT uq_user_department UNIQUE (user_id, department_id)
);

CREATE INDEX IF NOT EXISTS idx_user_department_user ON user_department(user_id);
CREATE INDEX IF NOT EXISTS idx_user_department_dept ON user_department(department_id);

-- ========================================
-- 3. Daily Update Log table
-- ========================================
CREATE TABLE IF NOT EXISTS daily_update_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    telegram_username VARCHAR(100),
    update_date DATE NOT NULL,
    update_content TEXT NOT NULL,
    telegram_message_id VARCHAR(100),
    is_valid BOOLEAN DEFAULT TRUE,
    parsed_at TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_update_log_user ON daily_update_log(user_id);
CREATE INDEX IF NOT EXISTS idx_update_log_date ON daily_update_log(update_date);
CREATE INDEX IF NOT EXISTS idx_update_log_user_date ON daily_update_log(user_id, update_date);

-- ========================================
-- 4. Update Configuration table
-- ========================================
CREATE TABLE IF NOT EXISTS update_config (
    id SERIAL PRIMARY KEY,
    working_days_per_week INTEGER DEFAULT 6,
    min_update_length INTEGER DEFAULT 20,
    update_deadline_hour INTEGER DEFAULT 23,
    telegram_channel_id VARCHAR(100),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert default configuration
INSERT INTO update_config (working_days_per_week, min_update_length, update_deadline_hour)
VALUES (6, 20, 23)
ON CONFLICT DO NOTHING;

-- ========================================
-- 5. Missed Update Notification table
-- ========================================
CREATE TABLE IF NOT EXISTS missed_update_notification (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    missed_date DATE NOT NULL,
    notified_at TIMESTAMP DEFAULT NOW(),
    notification_sent BOOLEAN DEFAULT FALSE,
    CONSTRAINT uq_user_missed_date UNIQUE (user_id, missed_date)
);

CREATE INDEX IF NOT EXISTS idx_missed_notif_user ON missed_update_notification(user_id);
CREATE INDEX IF NOT EXISTS idx_missed_notif_date ON missed_update_notification(missed_date);

-- ========================================
-- NOTES:
-- ========================================
-- This migration creates the update tracking system:
-- 1. department - stores departments (Dev Team, Marketing, Local Sales, International Sales)
-- 2. user_department - maps users to their departments
-- 3. daily_update_log - stores daily updates from Telegram channel
-- 4. update_config - system-wide configuration for update tracking
-- 5. missed_update_notification - tracks missed update notifications
--
-- After running this migration:
-- 1. Set up Telegram bot to monitor updates channel
-- 2. Configure telegram_channel_id in update_config table
-- 3. Assign users to departments via user_department table
-- 4. Bot will automatically parse updates with format:
--    Update for December 16
--    #username
--    - task 1
--    - task 2
--
-- APIs to implement:
-- POST /updates/telegram-webhook - receives updates from Telegram bot
-- GET /updates/stats - get update statistics (weekly, monthly, quarterly)
-- GET /updates/employee/{user_id} - get specific employee update stats
-- GET /updates/department/{dept_id} - get department-wide statistics
-- GET /dashboard/main - main dashboard with company-wide stats
-- GET /dashboard/employee - employee dashboard (salary, updates, etc.)
