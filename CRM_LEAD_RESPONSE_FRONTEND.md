# CRM Lead Response Metrics Frontend

## Maqsad

Lead `need_to_call` bo'lib tushgandan keyin operator qancha vaqtda status o'zgartirgani va note yozgani ko'rsatiladi.

SLA: `5 minut`.

## Customer Detail

Endpoint:

```http
GET /crm/detail/{customer_id}
```

Response ichida:

```json
{
  "lead_response_metrics": {
    "lead_created_at": "2026-07-28T14:10:00+05:00",
    "current_status": "contacted",
    "first_status_changed_at": "2026-07-28T14:17:30+05:00",
    "first_status_changed_to": "contacted",
    "response_minutes": 7.5,
    "response_human": "7.5 minut",
    "response_limit_minutes": 5,
    "is_late_response": true,
    "late_minutes": 2.5,
    "late_human": "2.5 minut",
    "first_note_at": null,
    "note_minutes": null,
    "note_human": null,
    "note_written": false,
    "status_changed": true,
    "status_changed_without_note": true,
    "message": "Status 7.5 minutda contacted ga o'zgartirildi, lekin note yozilmadi"
  }
}
```

## Customer List / CRM Dashboard

Endpoint:

```http
GET /crm/dashboard
GET /crm/customers/latest
```

Har bir customer item ichida `lead_response_metrics` keladi.

UI tavsiya:

```text
status_changed=false -> "Hali bog'lanilmagan"
is_late_response=false -> green badge "O'z vaqtida"
is_late_response=true -> red badge "Kech bog'lanildi"
status_changed_without_note=true -> warning badge "Note yozilmadi"
```

## CEO Dashboard

Endpoint:

```http
GET /ceo/dashboard
```

`statistics` ichida yangi fields:

```json
{
  "lead_response_limit_minutes": 5,
  "lead_response_total_count": 120,
  "lead_response_status_changed_count": 90,
  "lead_response_on_time_count": 55,
  "lead_response_late_count": 35,
  "lead_response_no_status_change_count": 30,
  "lead_response_note_written_count": 70,
  "lead_response_note_missing_count": 50,
  "lead_response_status_changed_without_note_count": 20,
  "lead_response_average_minutes": 8.4,
  "lead_response_average_human": "8.4 minut",
  "lead_response_average_with_note_minutes": 7.8,
  "lead_response_average_with_note_human": "7.8 minut",
  "lead_response_average_note_minutes": 9.2,
  "lead_response_average_note_human": "9.2 minut",
  "lead_response_average_late_minutes": 6.1,
  "lead_response_average_late_human": "6.1 minut"
}
```

## Meaning

`lead_response_total_count`: need_to_call leadlar va need_to_call dan boshqa statusga o'tgan leadlar.

`lead_response_status_changed_count`: need_to_call dan boshqa statusga o'tkazilganlar.

`lead_response_on_time_count`: status 5 minut ichida o'zgarganlar.

`lead_response_late_count`: status 5 minutdan keyin o'zgarganlar.

`lead_response_no_status_change_count`: hali status o'zgarmaganlar.

`lead_response_note_written_count`: note bor leadlar.

`lead_response_note_missing_count`: note yo'q leadlar.

`lead_response_status_changed_without_note_count`: status o'zgargan, lekin note yozilmagan leadlar.

`lead_response_average_minutes`: status o'zgargan leadlarning o'rtacha bog'lanish vaqti.

`lead_response_average_with_note_minutes`: status o'zgargan va note bor leadlarning o'rtacha bog'lanish vaqti.

`lead_response_average_note_minutes`: note yozilgan leadlarning o'rtacha note yozish vaqti.

`lead_response_average_late_minutes`: kech bog'lanilgan leadlarda o'rtacha kechikish.

## Detail Text

Frontend `message` ni to'g'ridan-to'g'ri ko'rsatishi mumkin.

Example:

```text
Status 12.0 minutda contacted ga o'zgartirildi, lekin note yozilmadi
```
