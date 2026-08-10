ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS check_in_at TIMESTAMPTZ NULL;
ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS check_out_at TIMESTAMPTZ NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'attendance_daily_record'
          AND column_name = 'source_updated_at'
          AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE attendance_daily_record
        ALTER COLUMN source_updated_at TYPE TIMESTAMPTZ
        USING source_updated_at AT TIME ZONE 'Asia/Tashkent';
    END IF;
END $$;

ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS shift_id VARCHAR(100) NULL;
ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS came_event_id VARCHAR(100) NULL;
ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS gone_event_id VARCHAR(100) NULL;
ALTER TABLE attendance_daily_record ADD COLUMN IF NOT EXISTS event_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE attendance_daily_record ALTER COLUMN source_system SET DEFAULT 'faceid';
UPDATE attendance_daily_record SET source_system = 'faceid' WHERE source_system IS NULL;
ALTER TABLE attendance_daily_record ALTER COLUMN source_system SET NOT NULL;
UPDATE attendance_daily_record SET source_session_id = CONCAT('legacy:', id) WHERE source_session_id IS NULL;
ALTER TABLE attendance_daily_record ALTER COLUMN source_session_id SET NOT NULL;
UPDATE attendance_daily_record SET source_updated_at = COALESCE(updated_at, created_at, NOW()) WHERE source_updated_at IS NULL;
ALTER TABLE attendance_daily_record ALTER COLUMN source_updated_at SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_daily_record_source_session_idx ON attendance_daily_record(source_system, source_session_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_daily_record_source_employee_date_idx ON attendance_daily_record(source_system, employee_id, attendance_date);
CREATE INDEX IF NOT EXISTS idx_attendance_daily_record_source_system ON attendance_daily_record(source_system);
CREATE INDEX IF NOT EXISTS idx_attendance_daily_record_status ON attendance_daily_record(status);

ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS source_event_id VARCHAR(100) NULL;
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'auto';
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS face_confidence NUMERIC(5,4) NULL;
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS photo_available BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS photo_url TEXT NULL;
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS manual_created_by VARCHAR(150) NULL;
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS manual_created_at TIMESTAMPTZ NULL;
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS manual_comment TEXT NULL;
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS source_created_at TIMESTAMPTZ NULL;
ALTER TABLE attendance_raw_event ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW();
UPDATE attendance_raw_event SET source_system = 'faceid' WHERE source_system IS NULL;
ALTER TABLE attendance_raw_event ALTER COLUMN source_system SET DEFAULT 'faceid';
ALTER TABLE attendance_raw_event ALTER COLUMN source_system SET NOT NULL;
UPDATE attendance_raw_event SET source_event_id = CONCAT('legacy:', id) WHERE source_event_id IS NULL;
ALTER TABLE attendance_raw_event ALTER COLUMN source_event_id SET NOT NULL;
UPDATE attendance_raw_event SET source_created_at = COALESCE(source_created_at, created_at, NOW()) WHERE source_created_at IS NULL;
ALTER TABLE attendance_raw_event ALTER COLUMN source_created_at SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'attendance_raw_event'
          AND column_name = 'event_time'
          AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE attendance_raw_event
        ALTER COLUMN event_time TYPE TIMESTAMPTZ
        USING event_time AT TIME ZONE 'Asia/Tashkent';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_raw_event_source_event_idx ON attendance_raw_event(source_system, source_event_id);
CREATE INDEX IF NOT EXISTS idx_attendance_raw_event_source_system ON attendance_raw_event(source_system);
