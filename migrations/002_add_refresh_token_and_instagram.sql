-- Migration: Add Refresh Token and Instagram Statistics
-- Date: 2025-12-15
-- Description: Adds refresh_token table and instagram_account/instagram_stats tables

-- ========================================
-- 1. Refresh Token Table
-- ========================================
CREATE TABLE IF NOT EXISTS refresh_token (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    token VARCHAR(500) NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    device_info VARCHAR(255)
);

-- Indexes for refresh_token
CREATE INDEX IF NOT EXISTS idx_refresh_token_user ON refresh_token(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_token_token ON refresh_token(token);
CREATE INDEX IF NOT EXISTS idx_refresh_token_active ON refresh_token(is_active);
CREATE INDEX IF NOT EXISTS idx_refresh_token_expires ON refresh_token(expires_at);

-- ========================================
-- 2. Instagram Account Table
-- ========================================
CREATE TABLE IF NOT EXISTS instagram_account (
    id SERIAL PRIMARY KEY,
    account_username VARCHAR(255) NOT NULL,
    instagram_business_account_id VARCHAR(255),
    facebook_page_id VARCHAR(255),
    access_token VARCHAR(500),
    token_expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    last_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ========================================
-- 3. Instagram Stats Table (Daily snapshots)
-- ========================================
CREATE TABLE IF NOT EXISTS instagram_stats (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES instagram_account(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    followers_count INTEGER NOT NULL,
    following_count INTEGER,
    media_count INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_instagram_stats_account_date UNIQUE (account_id, date)
);

-- Indexes for instagram_stats
CREATE INDEX IF NOT EXISTS idx_instagram_stats_date ON instagram_stats(date);
CREATE INDEX IF NOT EXISTS idx_instagram_stats_account_date ON instagram_stats(account_id, date);

-- ========================================
-- NOTES:
-- ========================================
-- This migration adds:
-- 1. refresh_token table for JWT refresh token management (15 days expiry)
-- 2. instagram_account table for Instagram Business account configuration
-- 3. instagram_stats table for daily follower count snapshots
--
-- After running this migration:
-- 1. Set up Instagram account via POST /instagram/setup
-- 2. Sync data via POST /instagram/sync (daily cron job recommended)
-- 3. View growth stats via GET /instagram/growth
