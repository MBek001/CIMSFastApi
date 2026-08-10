# Attendance Device Integration

Base URL:

```text
https://api.project.cims.cognilabs.org
```

Auth header:

```text
X-Attendance-Key: <ATTENDANCE_API_KEY>
```

All timestamps must be timezone-aware ISO 8601. Use `+05:00` for Tashkent time.

Valid daily statuses:

```text
present, late, absent, incomplete
```

Valid raw event actions:

```text
came, gone
```

Valid raw event sources:

```text
auto, manual
```

## 1. Get CIMS Users

Use this to map device users to CIMS employee IDs.

```http
GET /attendance/users?page=1&page_size=500&is_active=true
X-Attendance-Key: <ATTENDANCE_API_KEY>
```

Optional filters:

```text
search
is_active
page
page_size
```

Response:

```json
{
  "items": [
    {
      "id": 18,
      "name": "Ali",
      "surname": "Valiyev",
      "full_name": "Ali Valiyev",
      "email": "ali@example.com",
      "department": null,
      "position": "Backend developer",
      "role": "employee",
      "role_name": null,
      "is_active": true
    }
  ],
  "page": 1,
  "page_size": 500,
  "total_count": 1
}
```

## 2. Send Raw Events

Use this for every device check-in/check-out event.

Endpoint:

```http
POST /attendance/raw-events/bulk-upsert
X-Attendance-Key: <ATTENDANCE_API_KEY>
Content-Type: application/json
```

Payload:

```json
{
  "events": [
    {
      "source_system": "faceid",
      "source_event_id": "device-1-event-987654",
      "employee_id": 18,
      "event_time": "2026-08-10T09:05:12+05:00",
      "action": "came",
      "source": "auto",
      "terminal_ip": "192.168.1.50",
      "face_confidence": 0.9821,
      "photo_available": true,
      "photo_url": "https://device.example/photos/987654.jpg",
      "is_manual": false,
      "manual_created_by": null,
      "manual_created_at": null,
      "manual_comment": null,
      "source_created_at": "2026-08-10T09:05:13+05:00"
    }
  ]
}
```

Response:

```json
{
  "success_count": 1,
  "failed_count": 0,
  "results": [
    {
      "success": true,
      "source_event_id": "device-1-event-987654",
      "employee_id": 18,
      "event_id": 120
    }
  ]
}
```

Important:

- `source_event_id` must be stable and unique per device event.
- If the same event is sent again, backend updates it, not duplicates it.
- If internet fails, save event locally and retry later with the same `source_event_id`.
- Bulk endpoint supports partial success. Retry only failed rows.

## 3. Send Daily Records

Use this after calculating the day result from raw events.

Endpoint:

```http
POST /attendance/daily-records/bulk-upsert
X-Attendance-Key: <ATTENDANCE_API_KEY>
Content-Type: application/json
```

Payload:

```json
{
  "records": [
    {
      "source_system": "faceid",
      "source_session_id": "faceid-18-2026-08-10",
      "employee_id": 18,
      "attendance_date": "2026-08-10",
      "check_in_at": "2026-08-10T09:05:12+05:00",
      "check_out_at": "2026-08-10T18:22:41+05:00",
      "check_in_time": "09:05:12",
      "check_out_time": "18:22:41",
      "worked_minutes": 557,
      "worked_hours_decimal": 9.28,
      "status": "present",
      "shift_id": "default",
      "shift_name": "Default shift",
      "is_manual": false,
      "came_event_id": "device-1-event-987654",
      "gone_event_id": "device-1-event-987699",
      "event_ids": [
        "device-1-event-987654",
        "device-1-event-987699"
      ],
      "note": null,
      "source_updated_at": "2026-08-10T18:22:45+05:00"
    }
  ]
}
```

Response:

```json
{
  "success_count": 1,
  "failed_count": 0,
  "results": [
    {
      "success": true,
      "employee_id": 18,
      "attendance_date": "2026-08-10",
      "source_session_id": "faceid-18-2026-08-10",
      "record_id": 77
    }
  ]
}
```

Important:

- `source_session_id` must be stable and unique per employee/day/source.
- Recommended format: `faceid-{employee_id}-{YYYY-MM-DD}`.
- If daily record is sent again, backend updates existing row.
- `worked_minutes` is authoritative. Backend accepts night shift when `check_out_time` is earlier than `check_in_time`.
- Do not send timezone-naive datetime values.

## 4. Upsert One Daily Record

Use this if the device sends one daily record at a time.

```http
PUT /attendance/daily-records/{employee_id}/{attendance_date}
X-Attendance-Key: <ATTENDANCE_API_KEY>
Content-Type: application/json
```

Example:

```http
PUT /attendance/daily-records/18/2026-08-10
```

Body is the same object as one item from `records`.

## 5. Read Daily Records

```http
GET /attendance/daily-records?page=1&page_size=100&year=2026&month=8
X-Attendance-Key: <ATTENDANCE_API_KEY>
```

Optional filters:

```text
employee_id
date_from
date_to
year
month
day
status
source_system
is_manual
page
page_size
```

## 6. Patch Or Soft Delete Daily Record

```http
PATCH /attendance/daily-records/{employee_id}/{attendance_date}
X-Attendance-Key: <ATTENDANCE_API_KEY>
Content-Type: application/json
```

Patch example:

```json
{
  "status": "incomplete",
  "note": "Check-out event missing",
  "source_updated_at": "2026-08-10T19:00:00+05:00"
}
```

Soft delete example:

```json
{
  "is_deleted": true,
  "delete_reason": "Wrong employee mapping",
  "source_updated_at": "2026-08-10T19:10:00+05:00"
}
```

## 7. Health Check

```http
GET /health
```

Response:

```json
{
  "status": "ok",
  "time": "2026-08-10T13:55:14.045118+05:00"
}
```

## Required Device Logic

1. Store `ATTENDANCE_API_KEY` securely.
2. Fetch users from `/attendance/users`.
3. Match device person to CIMS `employee_id`.
4. Send every raw event to `/attendance/raw-events/bulk-upsert`.
5. Calculate daily result per employee/day.
6. Send daily result to `/attendance/daily-records/bulk-upsert`.
7. On network/API failure, keep unsent data locally.
8. Retry failed records with same `source_event_id` and `source_session_id`.
9. Do not create random IDs on retry.
10. Use `+05:00` timezone in all datetime fields.
