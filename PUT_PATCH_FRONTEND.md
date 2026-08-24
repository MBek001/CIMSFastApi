# PUT/PATCH update API update

Backendda `PUT` bor update endpointlarga `PATCH` ham qo'shildi.

## Asosiy qoida

- `PATCH` payload formatlari `PUT` bilan bir xil.
- Frontend edit/save uchun `PUT` o'rniga `PATCH` ishlatsa bo'ladi.
- Mavjud `PUT` endpointlar o'chirilmadi, eski frontend buzilmaydi.

## CEO users

```http
PATCH /ceo/users/{user_id}
```

Misol:

```http
PATCH https://cims.cognilabs.org/api/ceo/users/5
```

Body `PUT /ceo/users/{user_id}` bilan bir xil.

## Qo'shilgan PATCH endpointlar

- `PATCH /ceo/users/{user_id}`
- `PATCH /ceo/payments/{payment_id}`
- `PATCH /ceo/company-payments/{payment_id}`
- `PATCH /ceo/users/{user_id}/permissions`
- `PATCH /finance/{finance_id}`
- `PATCH /crm/customers/{customer_id}`
- `PATCH /crm/customers/{customer_id}/notes/{note_id}`
- `PATCH /wordpress/projects/{project_id}`
- `PATCH /attendance/records/{attendance_id}`
- `PATCH /attendance/daily-records/{employee_id}/{attendance_date}`
- `PATCH /management/pages/{page_id}`
- `PATCH /management/statuses/{status_id}`
- `PATCH /management/roles/{role_id}`
- `PATCH /cognilabsai/conversations/{conversation_id}/follow-up`
- `PATCH /updates/member/mistakes/{mistake_id}`
- `PATCH /updates/member/delivery-bonuses/{bonus_id}`
- `PATCH /updates/member/update/{update_id}`
- `PATCH /update-tracking/workday-overrides/{override_id}`

## Frontend action

Edit modal/formlarda update request `PUT` bo'lsa, xohishga qarab `PATCH`ga o'tkazish mumkin. Backend ikkisini ham qabul qiladi.
