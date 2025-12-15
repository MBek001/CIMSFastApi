# 🚀 CIMS FastAPI - New Features Update

## Yangi Xususiyatlar (2025-12-15)

### 1️⃣ **Dinamik Customer Status Boshqaruvi**

CEO endi mijoz statuslarini o'zi boshqara oladi - yangi status qo'shish, tahrirlash va o'chirish.

#### API Endpoints:

```bash
# Barcha statuslarni ko'rish
GET /management/statuses

# Yangi status yaratish (faqat CEO)
POST /management/statuses
{
  "name": "negotiating",
  "display_name": "Negotiating",
  "description": "In negotiation phase",
  "color": "#FF6B6B",
  "order": 7,
  "is_active": true,
  "is_system": false
}

# Statusni yangilash (faqat CEO)
PUT /management/statuses/{status_id}
{
  "display_name": "Under Negotiation",
  "color": "#FF8888"
}

# Statusni o'chirish (faqat CEO, system statuslar o'chirilmaydi)
DELETE /management/statuses/{status_id}
```

#### Xususiyatlar:
- ✅ CEO o'zi yangi statuslar yaratadi
- ✅ Har bir statusning rangi va tartibi bor (UI uchun)
- ✅ System statuslarni o'chirish mumkin emas
- ✅ Foydalanilayotgan statuslarni o'chirishdan oldin ogohlantirish

---

### 2️⃣ **Dinamik User Role Boshqaruvi**

CEO endi foydalanuvchi rollarini ham boshqara oladi.

#### API Endpoints:

```bash
# Barcha rollarni ko'rish
GET /management/roles

# Yangi rol yaratish (faqat CEO)
POST /management/roles
{
  "name": "project_manager",
  "display_name": "Project Manager",
  "description": "Manages projects and teams",
  "is_active": true,
  "is_system": false
}

# Rolni yangilash (faqat CEO)
PUT /management/roles/{role_id}
{
  "display_name": "Senior Project Manager",
  "description": "Senior PM with extended permissions"
}

# Rolni o'chirish (faqat CEO, system rollar o'chirilmaydi)
DELETE /management/roles/{role_id}
```

#### Default Rollar:
- CEO (system)
- Financial Director (system)
- Sales Manager (system) - **YANGI!**
- Member (system)
- Customer (system)

---

### 3️⃣ **Sales Manager Avtomatik Assign Tizimi**

CRM tizimida Sales Managerlar ro'yxati va leadlarni avtomatik navbat bilan (round-robin) assign qilish.

#### API Endpoints:

```bash
# Barcha Sales Managerlarni ko'rish
GET /crm/sales-managers
Response:
[
  {
    "id": 5,
    "email": "john@example.com",
    "name": "John",
    "surname": "Doe",
    "assigned_leads_count": 15
  }
]

# Sales Manager qo'lda assign qilish
POST /crm/assign-sales-manager
{
  "customer_id": 123,
  "sales_manager_id": 5
}

# Mijozning Sales Managerini ko'rish
GET /crm/customer/{customer_id}/sales-manager
Response:
{
  "assignment_id": 42,
  "customer_id": 123,
  "sales_manager": {
    "id": 5,
    "email": "john@example.com",
    "name": "John",
    "surname": "Doe"
  },
  "assigned_at": "2025-12-15T10:30:00",
  "assigned_by": 1
}
```

#### Qanday Ishlaydi:
1. Yangi mijoz qo'shilganda avtomatik Sales Manager assign qilinadi
2. Round-robin algoritm - har bir SM ga navbat bilan
3. Faqat faol (is_active=true) Sales Managerlar
4. Qo'lda ham assign qilish mumkin

---

### 4️⃣ **Conversion Rate Analytics**

Oxirgi 100 ta leaddan nechta "Project Started" holatiga o'tganini ko'rsatadi.

#### API Endpoint:

```bash
# Conversion rate ko'rish
GET /crm/conversion-rate
Response:
{
  "total_customers": 100,
  "project_started_count": 23,
  "conversion_rate": 23.0,
  "period": "Oxirgi 100 ta lead"
}
```

#### Hisoblash:
```
Conversion Rate = (project_started_count / total_customers) × 100
```

---

## 🗄️ Database O'zgarishlari

### Yangi Jadvallar:

1. **customer_status** - Dinamik statuslar
2. **user_role** - Dinamik rollar
3. **sales_manager_assignment** - SM assignment tracking
4. **sales_manager_counter** - Round-robin counter

### Yangi Ustunlar:

- `customer.status_name` - Dinamik status (String)
- `user.role_name` - Dinamik rol (String)

### Migration:

```bash
# PostgreSQL ga ulanish
psql -U your_user -d cims_db

# Migration faylini ishga tushirish
\i migrations/001_add_dynamic_management.sql

# Mavjud ma'lumotlarni migratsiya qilish
UPDATE customer SET status_name = status::text WHERE status_name IS NULL;
UPDATE "user" SET role_name = role::text WHERE role_name IS NULL;
```

---

## 📊 Yangi Fayllar

```
CIMSFastApi/
├── routers/
│   ├── management.py              # Status & Role management
│   └── crm_sales_manager.py       # Sales Manager & Conversion
├── schemes/
│   └── schemes_management.py      # Pydantic schemas
├── models/
│   ├── admin_models.py            # Yangilandi (4 ta yangi jadval)
│   └── user_models.py             # Yangilandi (role_name qo'shildi)
├── migrations/
│   └── 001_add_dynamic_management.sql
└── FEATURE_UPDATE.md              # Bu fayl
```

---

## 🧪 Test Qilish

### 1. Status Management Test:

```bash
# Login as CEO
TOKEN="your_ceo_token"

# Get all statuses
curl -X GET "http://localhost:8000/management/statuses" \
  -H "Authorization: Bearer $TOKEN"

# Create new status
curl -X POST "http://localhost:8000/management/statuses" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "on_hold",
    "display_name": "On Hold",
    "color": "#FFA500"
  }'
```

### 2. Sales Manager Test:

```bash
# Get all sales managers
curl -X GET "http://localhost:8000/crm/sales-managers" \
  -H "Authorization: Bearer $TOKEN"

# Assign manually
curl -X POST "http://localhost:8000/crm/assign-sales-manager" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": 1,
    "sales_manager_id": 5
  }'
```

### 3. Conversion Rate Test:

```bash
# Get conversion rate
curl -X GET "http://localhost:8000/crm/conversion-rate" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 🔐 Xavfsizlik

- ✅ Faqat CEO status va rol boshqara oladi
- ✅ System status/rollarni o'chirish mumkin emas
- ✅ Foydalanilayotgan status/rollarni o'chirishda ogohlantirish
- ✅ Barcha operatsiyalar JWT token bilan himoyalangan
- ✅ Role-based access control (RBAC)

---

## 📝 Keyingi Qadamlar

1. ✅ Migration scriptini ishga tushirish
2. ✅ Mavjud ma'lumotlarni yangi ustunlarga ko'chirish
3. ✅ Frontend ga yangi endpointlarni ulash
4. ✅ Sales Manager ro'yxatini yaratish (role = "sales_manager")
5. ✅ Test qilish va production ga deploy

---

## ❓ Savollar

Agar savol bo'lsa, issue oching yoki bizga murojaat qiling.

**API Documentation:** `http://localhost:8000/docs`
