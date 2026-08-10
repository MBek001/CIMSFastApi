# CIMS Project Team / Task Update

Backend changes:

- Teams:
  - `GET /projects/teams`
  - `POST /projects/teams`
  - `GET /projects/teams/{team_id}`
  - `PATCH /projects/teams/{team_id}`
  - `DELETE /projects/teams/{team_id}`

- Project:
  - `project.team_id`
  - `project.deadline`
  - `project.telegram_group_id`
  - `PATCH /projects/{project_id}/telegram-group`

- Task/card:
  - `due_date` = task deadline
  - `completed_at`
  - `completion_duration_seconds`
  - `current_status_duration_seconds`
  - `status_history[]`

- Status history:
  - Card boshqa column/statusga move bo‘lsa backend eski status vaqtini yopadi.
  - Yangi status vaqtini ochadi.
  - `Done` columniga kirsa `completed_at` yoziladi.

- Updates:
  - Telegram update bot disabled.
  - Card move bo‘lsa system o‘sha user uchun `daily_update_log` ga update yozadi.

- Telegram group task:
  - Projectga `telegram_group_id` bind qilinadi.
  - Bot token server `.env`: `PROJECT_TASK_BOT_TOKEN`.
  - Group command:
    - `/backend @telegram_username deadline:2026-08-15 Task text`
    - `/frontend @telegram_username deadline:2026-08-15 18:00 Task text`
  - Backend/Frontend board alohida yaratiladi.
  - Task birinchi column (`To Do`) ga tushadi.
  - Assignee topilsa userga Telegram xabar boradi.
