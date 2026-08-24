# CIMS Project Team + Telegram Task Bot

## Project form

Project create/edit formda quyilar bo'lishi kerak:

- `team_id`: team tanlanadi. Backend/frontend user alohida tanlanmaydi.
- `deadline`: project umumiy deadline.
- `telegram_group_id` yoki `telegram_group_chat_id`: project Telegram group chat id.

Form-data endpointlar:

- `POST /projects`
- `PATCH /projects/{project_id}`
- `PATCH /projects/{project_id}/telegram-group`

`telegram_group_id` va `telegram_group_chat_id` ikkalasi ham backendda qabul qilinadi. Response field hozircha `telegram_group_id`.

## Team logic

Projectga team tanlansa backend team memberlarni project memberlarga ham qo'shadi.

Task bot guruhdan task yaratganda assignee team ichidan topiladi:

- `/add_task_front` yoki `/frontend`: frontend user.
- `/add_task_back` yoki `/backend`: backend user.
- Teamda 1ta user bo'lsa shu user frontend ham backend ham bo'ladi.

Frontend/backend aniqlash user `job_title`, `role_name`, `company_code`, `email` ichidagi so'zlar orqali bo'ladi.

## Bot registration

Har bir developer private chatda `@cognilabs_tasks_bot` ga `/start` yuboradi.

Agar Telegram username CIMS `telegram_id` bilan mos bo'lmasa:

```text
/start email@example.com
```

Backend user `chat_id` ni saqlaydi. Shundan keyin task notification shu botdan boradi.

Server env:

- `PROJECT_TASK_BOT_TOKEN`: task bot token.
- `PROJECT_TASK_REPORT_CHAT_ID`: Cognilabs group chat id.
- `PROJECT_TASK_REPORT_THREAD_ID`: task report thread id.

## Group commands

Bot project Telegram guruhiga admin qilib qo'shiladi.

Yangi message ichida command:

```text
Login page responsive fix qilish kerak /add_task_front 25.08
Payment API bug fix /add_task_back 25.08 19:00
```

Oldingi messagega reply qilib task qilish:

```text
/add_task_front 25.08
/add_task_back 25.08 19:00
```

Legacy commandlar ham ishlaydi:

```text
/frontend 2026-08-25 19:00 Task text
/backend deadline:2026-08-25 19:00 Task text
```

Date qoidasi:

- `25.08` = joriy yil, soat `18:00`.
- `25.08 19:00` = shu sana va vaqt.
- `2026-08-25 19:00` ham ishlaydi.

## Task response

Task card responsega qo'shilgan fieldlar:

- `telegram_source_chat_id`
- `telegram_source_message_id`
- `telegram_source_command`
- `telegram_source_kind`

Bu fieldlar task Telegramdan yaratilganini ko'rsatadi.

## Duplicate rule

Reply qilingan bir xil Telegram message uchun bir xil yo'nalishda task qayta yaratilmaydi.

Backend groupga:

```text
Bu message oldin task qilingan: #123
```

deb qaytaradi.

## User task report

Cognilabs groupdagi maxsus threadda user nomi command qilib yozilsa user tasklari chiqadi:

```text
/Ahmad
/Abdugani
/Alisher
```

Backend userni quyidagilar bo'yicha topadi:

- `telegram_id`
- email prefix
- name/surname

Report ichida:

- user full name
- role/job title
- umumiy task count
- project bo'yicha group
- ochiq/done task count
- card id
- task title
- board
- current status column
- deadline
- overdue flag
- priority

## Private notification

Developerga private bot xabari shunday formatda boradi:

```text
Yangi task

Project: Project nomi
Task:
Task matni

Izoh:
Qo'shimcha izoh

Priority: Medium
Muddat: 2026-08-25 19:00
Kim berdi: username
```
