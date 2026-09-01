# Developer KPI + Auto Salary Frontend

## Access

API prefix: `/developer-kpi`

Write access:
- CEO
- Dev Team Leader

Member:
- only own salary estimate/snapshot can be viewed

## Core Flow

1. CEO/Dev Team Leader creates `Feature`.
2. Feature gets `points`, `owner_id`, `due_date`, `acceptance_criteria`.
3. Before work starts, feature is locked.
4. Accepted feature gives Delivery + Deadline KPI.
5. Kanban card moved to `Refix` or `Reopen` gives automatic Quality penalty.
6. FaceID attendance + per-user work schedule gives Discipline score.
7. Project actual delivery date gives automatic deadline deduction candidate if delay is more than 3 business days.
8. Salary estimate recalculates live.
9. Every month last day `23:55` backend freezes monthly salary snapshot automatically.

## Formula

Final KPI:

`delivery * 35% + deadline * 20% + quality * 20% + team * 15% + discipline * 10%`

Salary:

`user.default_salary + (user.default_salary * 15% * final_kpi / 100) - approved_deductions`

## Work Schedule

`POST /developer-kpi/work-schedules`

```json
{
  "user_id": 18,
  "weekday": 0,
  "work_start_time": "11:00:00",
  "work_end_time": "21:00:00",
  "free_start_time": "13:00:00",
  "free_end_time": "15:00:00",
  "late_grace_minutes": 10,
  "is_active": true
}
```

`weekday`: Monday `0`, Sunday `6`.

`GET /developer-kpi/work-schedules?user_id=18`

## Features

`POST /developer-kpi/features`

```json
{
  "project_id": 14,
  "title": "Login API",
  "description": "JWT login endpoint",
  "acceptance_criteria": "Valid login returns access token",
  "points": 5,
  "owner_id": 18,
  "frontend_percent": 0,
  "backend_percent": 100,
  "due_date": "2026-09-20",
  "status": "planned",
  "is_mandatory": true,
  "lock_now": true
}
```

Points allowed: `1,2,3,5,8,13`.

`GET /developer-kpi/features?year=2026&month=9&owner_id=18`

`PATCH /developer-kpi/features/{feature_id}`

Locked feature:
- Dev Team Leader cannot edit
- CEO can edit

`POST /developer-kpi/features/{feature_id}/accept`

```json
{
  "accepted_at": "2026-09-19T18:30:00"
}
```

`accepted_at` optional. Backend uses current time if empty.

## Blocked Period

`POST /developer-kpi/blocked-periods`

```json
{
  "project_id": 14,
  "feature_id": 3,
  "employee_id": 18,
  "started_at": "2026-09-10T11:00:00",
  "ended_at": "2026-09-12T18:00:00",
  "reason": "Client API access not provided",
  "dependency": "Client",
  "evidence_url": "https://...",
  "is_external": true
}
```

Approve:

`PATCH /developer-kpi/blocked-periods/{blocked_id}`

```json
{
  "approval_status": "approved"
}
```

Approved external blocked business days are removed from deadline delay.

## Project Delivery

Project list/detail now returns:

```json
{
  "actual_delivery_date": "2026-09-29",
  "delivery_status": "delivered",
  "approved_blocked_days": 2,
  "real_delay_days": 4
}
```

Update delivery KPI fields:

`PATCH /developer-kpi/projects/{project_id}/delivery`

```json
{
  "actual_delivery_date": "2026-09-29",
  "delivery_status": "delivered",
  "approved_blocked_days": 2
}
```

Backend calculates `real_delay_days` using project `deadline`, actual delivery date, Sunday-off business days, and approved blocked days.

If `real_delay_days > 3`, backend creates `PROJECT_DEADLINE_DEDUCTION` candidate for feature owners in that project/month.

## Quality Events

Manual:

`POST /developer-kpi/quality-events`

```json
{
  "project_id": 14,
  "feature_id": 3,
  "card_id": 50,
  "employee_id": 18,
  "severity": "major",
  "source": "manual",
  "title": "Payment API bug",
  "description": "Client reached bug",
  "event_date": "2026-09-21",
  "confirmed": true,
  "is_duplicate": false,
  "external_cause": false
}
```

Severity values frontend can offer:
- `minor_qa_reopen`
- `major_qa_reopen`
- `prod_bug`
- `major_prod_bug`
- `critical_prod_incident`
- `functional`
- `major`
- `critical`

Automatic:
- card status history contains `Refix`
- card status history contains `Reopen`

Each auto event gives `-2` quality points.

## Salary Estimate

Single employee:

`GET /developer-kpi/salary-estimate?employee_id=18&year=2026&month=9`

All employees:

`GET /developer-kpi/salary-estimates?year=2026&month=9`

Response key fields:

```json
{
  "salary": {
    "base_salary": 1000,
    "max_kpi_fund": 150,
    "kpi_bonus": 130.5,
    "approved_deductions": 0,
    "expected_salary": 1130.5
  },
  "scores": {
    "delivery": 92,
    "deadline": 85,
    "quality": 95,
    "team": 88,
    "discipline": 98,
    "final_kpi": 91.2
  },
  "details": {
    "delivery": {},
    "quality": {},
    "discipline": {},
    "team": {},
    "deductions": []
  }
}
```

## Deductions

`GET /developer-kpi/deductions?year=2026&month=9&employee_id=18`

Approve/reject:

`PATCH /developer-kpi/deductions/{deduction_id}`

```json
{
  "status": "approved",
  "reason": "Approved by CEO"
}
```

Only `approved` deductions reduce salary.

## Snapshots

Auto freeze:
- every month last day
- `23:55`
- all active members

Manual freeze:

`POST /developer-kpi/snapshots/freeze?year=2026&month=9`

Single employee:

`POST /developer-kpi/snapshots/freeze?year=2026&month=9&employee_id=18`

List:

`GET /developer-kpi/snapshots?year=2026&month=9`

Snapshot is frozen monthly payroll copy. It stores source payload too.

## Suggested Pages

Add menu/page:
- Developer KPI

Tabs:
- Features
- Project Delivery
- Work Schedule
- Quality Events
- Blocked Periods
- Deductions
- Salary Estimates
- Frozen Snapshots

Employee profile/detail:
- current month KPI score
- expected salary
- delivery/deadline/quality/team/discipline breakdown
- feature list
- attendance discipline details

Project detail:
- KPI features list
- accepted points
- late features
- blocked periods
- quality events
