# 🔧 CIMS FastAPI - Bug Fixes and New Features (2025-12-15)

## Tuzatilgan Muammolar va Yangi Xususiyatlar

### 1️⃣ **CRM Dinamik Status Muammosi** ✅ FIXED

**Muammo:** POST `/crm/customers` endpointida yangi yaratilgan dinamik statuslar ko'rinmasdi - faqat eski enum statuslar chiqardi.

**Yechim:**
- `/crm/statuses/dynamic` endpoint qo'shildi - barcha aktiv dinamik statuslarni olish uchun
- `/crm/customers` endpoint yangilandi - endi string status qabul qiladi
- Auto-validation: dinamik status jadvalidan tekshiradi
- Backward compatible: eski enum statuslar ham ishlayveradi
- Auto-assign Sales Manager: yangi mijoz yaratilganda avtomatik SM assign qilinadi

**API Endpoints:**
```bash
# Barcha dinamik statuslarni olish (frontend dropdown uchun)
GET /crm/statuses/dynamic
Response:
[
  {
    "value": "contacted",
    "label": "Contacted",
    "color": "#3B82F6",
    "order": 1,
    "description": "Initial contact made"
  },
  ...
]

# Yangi mijoz yaratish (dinamik status bilan)
POST /crm/customers
Form Data:
  status: "contacted"  # Endi string (dinamik status name)
```

---

### 2️⃣ **Refresh Token Tizimi** ✅ NEW

**Talaba:** Access token bilan birga refresh token ham bo'lishi kerak (15 kun muddatli). Login va verify-email endpointlarida ikkalasini ham qaytarish kerak.

**Qo'shilgan Xususiyatlar:**

#### Database:
- **refresh_token** jadvali yaratildi
  - 15 kun muddatli
  - Secure random token (64 byte urlsafe)
  - Device tracking (optional)
  - Active/inactive status

#### API Endpoints:
```bash
# 1. Login - access va refresh token qaytaradi
POST /auth/login
Response:
{
  "access_token": "eyJ...",
  "refresh_token": "vH3kL...",
  "token_type": "bearer",
  "expires_in": 180000  # seconds
}

# 2. Email verification - access va refresh token qaytaradi
POST /auth/verify-email
Response:
{
  "access_token": "eyJ...",
  "refresh_token": "vH3kL...",
  "token_type": "bearer",
  "expires_in": 180000
}

# 3. Refresh token - yangi tokenlar olish (YANGI!)
POST /auth/refresh
Request:
{
  "refresh_token": "vH3kL..."
}
Response:
{
  "access_token": "eyJ...",  # Yangi access token
  "refresh_token": "pK9sD...",  # Yangi refresh token
  "token_type": "bearer",
  "expires_in": 180000
}

# 4. Logout - refresh tokenni bekor qilish (YANGI!)
POST /auth/logout
Request:
{
  "refresh_token": "vH3kL..."
}

# 5. Logout from all devices - barcha tokenlarni bekor qilish (YANGI!)
POST /auth/logout-all
Headers:
  Authorization: Bearer {access_token}
```

#### Token Muddatlari:
- **Access Token:** 3000 daqiqa (~2 kun) - backward compatibility uchun
- **Refresh Token:** 15 kun
- **Token Rotation:** Har safar refresh qilinganda yangi refresh token beriladi, eski bekor qilinadi

#### Xavfsizlik:
- Secure random token generation (secrets.token_urlsafe)
- Token expiry tracking
- Active/inactive status
- Device tracking (optional)
- Automatic cleanup for expired tokens

---

### 3️⃣ **Instagram Statistika Tizimi** ✅ NEW

**Talaba:** CEO Dashboardda Instagram account followerlar soni va oxirgi 7 kun, 1 oy, 3 oy, 6 oy, 1 yil davridagi o'sishni ko'rsatish.

**Qanday Ishlaydi:**
1. Instagram Business Account ga ulanish
2. Meta Graph API orqali follower soni olinadi
3. Har kuni bir marta bazaga saqlanadi
4. O'sish hisoblash: farqlar orqali

#### Database:
- **instagram_account** - Instagram Business account konfiguratsiyasi
- **instagram_stats** - Kunlik follower count snapshots

#### API Endpoints:

```bash
# 1. Instagram akkauntni sozlash (bir marta)
POST /instagram/setup
Request:
{
  "account_username": "your_username",
  "instagram_business_account_id": "17841...",
  "facebook_page_id": "10912...",
  "access_token": "EAAG..."  # Long-lived token (60 days)
}

# 2. Ma'lumotlarni sinxronlash (har kuni)
POST /instagram/sync
Response:
{
  "message": "Instagram ma'lumotlari muvaffaqiyatli sinxronlandi",
  "followers_count": 15234,
  "following_count": 450,
  "media_count": 123
}

# 3. O'sish statistikasini ko'rish
GET /instagram/growth
Response:
{
  "current_followers": 15234,
  "last_7_days": {
    "growth": 120,
    "growth_percentage": 0.79
  },
  "last_30_days": {
    "growth": 450,
    "growth_percentage": 3.04
  },
  "last_90_days": {
    "growth": 1200,
    "growth_percentage": 8.56
  },
  "last_180_days": {
    "growth": 2300,
    "growth_percentage": 17.80
  },
  "last_365_days": {
    "growth": 4500,
    "growth_percentage": 41.95
  }
}
```

#### Meta Graph API Setup:

1. **Facebook Developers App** yarating
2. **Permissions** so'rang:
   - `instagram_basic`
   - `instagram_manage_insights`
   - `pages_show_list`
   - `pages_read_engagement`
3. **Long-lived token** oling (60 kun)
4. **Instagram Business Account ID** ni oling:
   ```
   GET https://graph.facebook.com/v19.0/me/accounts?access_token=...
   GET https://graph.facebook.com/v19.0/{page-id}?fields=instagram_business_account
   ```

#### Cron Job (Recommended):
```bash
# Har kuni soat 00:00 da sinxronlash
0 0 * * * curl -X POST https://api.your-domain.com/instagram/sync \
  -H "Authorization: Bearer YOUR_CEO_TOKEN"
```

---

## 🗄️ Database Migrations

### Migration 002: Refresh Token va Instagram

```bash
# PostgreSQL ga ulanish
psql -U your_user -d cims_db

# Migration faylini ishga tushirish
\i migrations/002_add_refresh_token_and_instagram.sql
```

**Yangi Jadvallar:**
1. **refresh_token** - JWT refresh token boshqaruvi
2. **instagram_account** - Instagram Business account konfiguratsiyasi
3. **instagram_stats** - Kunlik follower count snapshots

---

## 📊 Yangi/O'zgargan Fayllar

```
CIMSFastApi/
├── routers/
│   ├── crm.py                      # Yangilandi (dinamik status)
│   ├── crm_dynamic_status.py       # YANGI
│   ├── auth.py                     # Yangilandi (refresh token)
│   └── instagram.py                # YANGI
├── models/
│   ├── user_models.py              # Yangilandi (refresh_token table)
│   └── instagram_models.py         # YANGI
├── utils/
│   └── instagram_service.py        # YANGI
├── schemes/
│   └── schemes_auth.py             # Yangilandi (TokenWithRefresh)
├── config.py                       # Yangilandi (REFRESH_TOKEN_EXPIRE_DAYS)
├── auth_utils/
│   └── auth_func.py                # Yangilandi (refresh token functions)
├── migrations/
│   └── 002_add_refresh_token_and_instagram.sql
├── run.py                          # Yangilandi (instagram router)
└── FIXES_UPDATE.md                 # Bu fayl
```

---

## 🧪 Test Qilish

### 1. CRM Dinamik Status Test:

```bash
TOKEN="your_ceo_token"

# 1. Dinamik statuslarni olish
curl -X GET "http://localhost:8000/crm/statuses/dynamic" \
  -H "Authorization: Bearer $TOKEN"

# 2. Yangi mijoz yaratish dinamik status bilan
curl -X POST "http://localhost:8000/crm/customers" \
  -H "Authorization: Bearer $TOKEN" \
  -F "full_name=John Doe" \
  -F "platform=Instagram" \
  -F "phone_number=+998901234567" \
  -F "status=contacted"  # Dinamik status
```

### 2. Refresh Token Test:

```bash
# 1. Login
curl -X POST "http://localhost:8000/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=user@example.com&password=password123"

# Response: access_token va refresh_token

# 2. Refresh token orqali yangi tokenlar olish
curl -X POST "http://localhost:8000/auth/refresh" \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "vH3kL..."}'

# 3. Logout
curl -X POST "http://localhost:8000/auth/logout" \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "vH3kL..."}'
```

### 3. Instagram Test:

```bash
TOKEN="your_ceo_token"

# 1. Setup Instagram account
curl -X POST "http://localhost:8000/instagram/setup" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "account_username": "your_account",
    "instagram_business_account_id": "17841...",
    "facebook_page_id": "10912...",
    "access_token": "EAAG..."
  }'

# 2. Sync Instagram data
curl -X POST "http://localhost:8000/instagram/sync" \
  -H "Authorization: Bearer $TOKEN"

# 3. Get growth statistics
curl -X GET "http://localhost:8000/instagram/growth" \
  -H "Authorization: Bearer $TOKEN"
```

---

## ⚠️ Muhim Eslatmalar

### CRM:
- Yangi mijoz yaratishda `status` parametri endi **string** (dinamik status name)
- `/crm/statuses/dynamic` dan aktiv statuslar ro'yxatini oling
- Mavjud enum statuslar ham ishlayveradi (backward compatible)

### Refresh Token:
- Access token muddati hali ham 3000 daqiqa (backward compatibility)
- Refresh token har safar yangilanadi (rotation)
- Logout qilishni unutmang (security)

### Instagram:
- Faqat **CEO** kira oladi
- **Instagram Business** account kerak
- **Long-lived token** (60 kun) kerak
- Har kuni `/instagram/sync` chaqiring (cron job)
- Token muddati tugashidan oldin yangilang

---

## 📝 Keyingi Qadamlar

1. ✅ Migration scriptini ishga tushiring
2. ✅ Frontend ga yangi endpointlarni ulang
3. ✅ Instagram Business account sozlang
4. ✅ Cron job qo'shing (Instagram sync)
5. ✅ Test qiling va production ga deploy qiling

---

## 🎉 Xulosa

**3 ta asosiy muammo/talaba to'liq hal qilindi:**

1. ✅ CRM dinamik status ko'rinish muammosi
2. ✅ Refresh token tizimi (15 kun)
3. ✅ Instagram statistika tracking

**Barcha o'zgarishlar production-ready va backward compatible!**

**API Documentation:** `http://localhost:8000/docs`
