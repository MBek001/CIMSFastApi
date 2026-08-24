# Attendance Frontend Auth Fix

## Muammo

Attendance GET API lar endi `X-Attendance-Key` ishlatmaydi.

Frontend hozir token yubormayotgani uchun backend quyidagini qaytaryapti:

```json
{
  "detail": "Token yuborilmagan. Authorization header kerak."
}
```

## Kerakli fix

Quyidagi GET requestlarda login access token yuborilsin:

- `GET /attendance/daily-records`
- `GET /attendance/daily-records/{employee_id}/{attendance_date}`
- `GET /attendance/users`

Header:

```http
Authorization: Bearer <access_token>
```

## Misol

```ts
await api.get("/attendance/daily-records", {
  params: {
    year: 2026,
    month: 8,
    page: 1,
    page_size: 100,
  },
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});
```

Yaxshiroq variant: axios/fetch interceptor ichida barcha CIMS API requestlarga token avtomatik qo'shilsin.

## Muhim

`X-Attendance-Key` faqat qurilma/write endpointlar uchun qolgan:

- `POST /attendance/daily-records/bulk-upsert`
- `PUT /attendance/daily-records/{employee_id}/{attendance_date}`
- `PATCH /attendance/daily-records/{employee_id}/{attendance_date}`
- raw event upload endpointlar

Frontend read-only attendance sahifasida `X-Attendance-Key` kerak emas.

## Employee nomi

`Employee #4` qilib chiqarish kerak emas. Attendance GET response endi employee nomini qaytaradi.

`GET /attendance/daily-records` item:

```json
{
  "employee_id": 4,
  "employee_name": "Ahmad Ziyovuddinov",
  "full_name": "Ahmad Ziyovuddinov",
  "employee": {
    "id": 4,
    "full_name": "Ahmad Ziyovuddinov",
    "name": "Ahmad",
    "surname": "Ziyovuddinov",
    "email": "example@gmail.com",
    "role": "Member",
    "role_name": "member",
    "job_title": "Backend"
  }
}
```

Frontend display:

```ts
const employeeName =
  item.employee?.full_name ||
  item.employee_name ||
  item.full_name ||
  `Employee #${item.employee_id}`;
```

## Bitta userning to'liq attendance

Yangi endpoint:

```http
GET /attendance/daily-records/user/{employee_id}
Authorization: Bearer <access_token>
```

Query:

- `year`
- `month`
- `date_from`
- `date_to`
- `day`
- `status`
- `source_system`
- `is_manual`
- `page`
- `page_size`

Misol:

```ts
await api.get(`/attendance/daily-records/user/${userId}`, {
  params: {
    year: 2026,
    month: 8,
    page: 1,
    page_size: 100,
  },
});
```

Response:

```json
{
  "employee": {
    "id": 4,
    "full_name": "Ahmad Ziyovuddinov"
  },
  "items": [],
  "stats": {
    "total_records": 0,
    "present_count": 0,
    "late_count": 0,
    "absent_count": 0,
    "incomplete_count": 0,
    "total_worked_minutes": 0,
    "total_worked_hours": 0,
    "avg_worked_minutes": 0
  },
  "page": 1,
  "page_size": 100,
  "total_count": 0
}
```
