CREATE TABLE IF NOT EXISTS project_team (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT NULL,
    created_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_team_member (
    id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES project_team(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT uq_project_team_member UNIQUE (team_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_team_member_team_id ON project_team_member(team_id);
CREATE INDEX IF NOT EXISTS idx_project_team_member_user_id ON project_team_member(user_id);

ALTER TABLE project ADD COLUMN IF NOT EXISTS team_id INTEGER NULL REFERENCES project_team(id) ON DELETE SET NULL;
ALTER TABLE project ADD COLUMN IF NOT EXISTS deadline TIMESTAMP NULL;
ALTER TABLE project ADD COLUMN IF NOT EXISTS telegram_group_id VARCHAR(100) NULL;

CREATE INDEX IF NOT EXISTS idx_project_team_id ON project(team_id);
CREATE INDEX IF NOT EXISTS idx_project_deadline ON project(deadline);
CREATE INDEX IF NOT EXISTS idx_project_telegram_group_id ON project(telegram_group_id);

ALTER TABLE project_board_card ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP NULL;

CREATE INDEX IF NOT EXISTS idx_project_board_card_due_date ON project_board_card(due_date);
CREATE INDEX IF NOT EXISTS idx_project_board_card_completed_at ON project_board_card(completed_at);

CREATE TABLE IF NOT EXISTS project_board_card_status_history (
    id SERIAL PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES project_board_card(id) ON DELETE CASCADE,
    column_id INTEGER NULL REFERENCES project_board_column(id) ON DELETE SET NULL,
    column_name VARCHAR(80) NOT NULL,
    entered_at TIMESTAMP NOT NULL DEFAULT NOW(),
    left_at TIMESTAMP NULL,
    duration_seconds INTEGER NULL,
    moved_by INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_project_card_status_history_card_id ON project_board_card_status_history(card_id);
CREATE INDEX IF NOT EXISTS idx_project_card_status_history_open ON project_board_card_status_history(card_id, left_at);

INSERT INTO project_board_card_status_history (card_id, column_id, column_name, entered_at, moved_by)
SELECT card.id, col.id, col.name, COALESCE(card.created_at, NOW()), card.created_by
FROM project_board_card AS card
JOIN project_board_column AS col ON col.id = card.column_id
WHERE NOT EXISTS (
    SELECT 1
    FROM project_board_card_status_history AS history
    WHERE history.card_id = card.id
);
