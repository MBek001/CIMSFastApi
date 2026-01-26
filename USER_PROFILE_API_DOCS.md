# User Profile & Statistics API Documentation

CIMS tizimida user o'z profilini va statistikasini ko'rish uchun APIlar.

## Authentication

Barcha endpointlar autentifikatsiya talab qiladi. Request headeriga JWT token qo'shish kerak:

```
Authorization: Bearer <your_jwt_token>
```

---

## API Endpoints

### 1. Get My Complete Profile

**Endpoint:** `GET /update-tracking/my-profile`

**Description:** User profilining to'liq ma'lumotlari bilan statistika

**Response:**
```json
{
  "user": {
    "id": 1,
    "name": "Ahmad Ahmadov",
    "telegram_id": "ahmad",
    "role": "Employee"
  },
  "statistics": {
    "total_updates": 45,
    "this_week": 5,
    "this_month": 20,
    "percentage_this_week": 100.0,
    "percentage_this_month": 74.1,
    "percentage_last_3_months": 68.5
  },
  "recent_updates": [
    {
      "date": "2025-12-28",
      "content": "1. Finance page fixed. 2. Create a student contract form...",
      "is_valid": true
    }
  ]
}
```

---

### 2. Get My Monthly Report

**Endpoint:** `GET /update-tracking/my-monthly-report`

**Query Parameters:**
- `month` (optional): Oy raqami (1-12), default: joriy oy
- `year` (optional): Yil (masalan 2025), default: joriy yil

**Example:**
```
GET /update-tracking/my-monthly-report?month=12&year=2025
```

**Response:**
```json
{
  "month": 12,
  "year": 2025,
  "month_name": "Dekabr",
  "working_days": 27,
  "sundays_count": 4,
  "total_days": 31,
  "statistics": {
    "update_days": 20,
    "missing_days": 7,
    "percentage": 74.1,
    "total_updates": 20
  },
  "ai_summary": "✅ YAXSHI (74.1%)\n\n📊 Statistika:\n   • 20/27 kun update bergan\n   • Kecha update bergan\n\n💡 Tahlil:\n   Yaxshi natija! Izchil ishlayapti.\n   Faol va izchil ishlamoqda\n\n📝 Tavsiya:\n   Yaxshi ish! 100% ga intilish mumkin.",
  "last_update": {
    "date": "2025-12-28",
    "content": "1. Finance page fixed. 2. Create a student contract form changed to the new version...",
    "days_ago": 1
  }
}
```

**AI Summary Format:**
- Grade: A'LO (90%+), YAXSHI (75%+), O'RTACHA (50%+), PAST (25%+), JUDA PAST (<25%)
- Statistika: qancha kun update bergan
- Tahlil: qisqacha baholash
- Tavsiya: nimalar qilish kerak

---

### 3. Get My Daily Calendar

**Endpoint:** `GET /update-tracking/my-daily-calendar`

**Query Parameters:**
- `month` (optional): Oy raqami (1-12), default: joriy oy
- `year` (optional): Yil (masalan 2025), default: joriy yil

**Example:**
```
GET /update-tracking/my-daily-calendar?month=12&year=2025
```

**Response:**
```json
{
  "month": 12,
  "year": 2025,
  "working_days": 27,
  "sundays_count": 4,
  "total_days": 31,
  "update_days": 20,
  "missing_days": 7,
  "percentage": 74.1,
  "calendar": [
    {
      "day": 1,
      "date": "2025-12-01",
      "weekday": "Dushanba",
      "is_sunday": false,
      "has_update": true,
      "update_content": "1. Fixed bugs in finance module...",
      "is_valid": true
    },
    {
      "day": 2,
      "date": "2025-12-02",
      "weekday": "Seshanba",
      "is_sunday": false,
      "has_update": false,
      "update_content": null,
      "is_valid": null
    },
    {
      "day": 7,
      "date": "2025-12-07",
      "weekday": "Yakshanba",
      "is_sunday": true,
      "has_update": false,
      "update_content": null,
      "is_valid": null
    }
  ]
}
```

**Calendar Object Fields:**
- `has_update`: true - update bergan, false - bermagan
- `is_sunday`: true - yakshanba (dam olish kuni)
- `update_content`: update matni (agar mavjud bo'lsa)
- `is_valid`: update valid yoki invalid

**Frontend uchun:**
- Green color: `has_update === true && !is_sunday`
- Yellow/Orange color: `has_update === false && !is_sunday`
- Gray color: `is_sunday === true`

---

### 4. Get My Performance Trends

**Endpoint:** `GET /update-tracking/my-trends`

**Description:** Oxirgi 6 oylik performance trend ko'rsatadi

**Response:**
```json
{
  "trends": [
    {
      "month": 7,
      "year": 2025,
      "month_name": "Iyul",
      "working_days": 27,
      "update_days": 18,
      "percentage": 66.7
    },
    {
      "month": 8,
      "year": 2025,
      "month_name": "Avgust",
      "working_days": 27,
      "update_days": 20,
      "percentage": 74.1
    },
    {
      "month": 9,
      "year": 2025,
      "month_name": "Sentabr",
      "working_days": 26,
      "update_days": 22,
      "percentage": 84.6
    }
  ],
  "average_percentage": 75.1
}
```

**Frontend uchun:**
- Chart (Line/Bar) yasash uchun `trends` massivini ishlating
- X-axis: `month_name` + `year`
- Y-axis: `percentage`
- Trend ko'rsatkichi: agar percentage o'sayotgan bo'lsa ↗️, pasayayotgan bo'lsa ↘️

---

### 5. Get My Basic Stats (Existing)

**Endpoint:** `GET /update-tracking/stats/me`

**Description:** Asosiy statistika (haftalik, oylik)

**Response:**
```json
{
  "user_id": 1,
  "user_name": "Ahmad Ahmadov",
  "total_updates": 45,
  "updates_this_week": 5,
  "updates_last_week": 4,
  "updates_this_month": 20,
  "updates_last_month": 18,
  "updates_last_3_months": 62,
  "percentage_this_week": 100.0,
  "percentage_last_week": 80.0,
  "percentage_this_month": 74.1,
  "percentage_last_3_months": 68.5,
  "expected_updates_per_week": 5
}
```

---

## Frontend Implementation Examples

### React/Next.js Example

```typescript
// Get user profile
const getUserProfile = async () => {
  const response = await fetch('/api/update-tracking/my-profile', {
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });
  const data = await response.json();
  return data;
};

// Get monthly report
const getMonthlyReport = async (month: number, year: number) => {
  const response = await fetch(
    `/api/update-tracking/my-monthly-report?month=${month}&year=${year}`,
    {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    }
  );
  const data = await response.json();
  return data;
};

// Get calendar
const getCalendar = async (month: number, year: number) => {
  const response = await fetch(
    `/api/update-tracking/my-daily-calendar?month=${month}&year=${year}`,
    {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    }
  );
  const data = await response.json();
  return data;
};
```

### Calendar Component Example

```typescript
interface CalendarDay {
  day: number;
  date: string;
  weekday: string;
  is_sunday: boolean;
  has_update: boolean;
  update_content: string | null;
  is_valid: boolean | null;
}

const CalendarView = ({ calendar }: { calendar: CalendarDay[] }) => {
  return (
    <div className="calendar-grid">
      {calendar.map((day) => (
        <div
          key={day.day}
          className={`calendar-day ${
            day.is_sunday
              ? 'sunday'
              : day.has_update
              ? 'has-update'
              : 'no-update'
          }`}
        >
          <div className="day-number">{day.day}</div>
          <div className="weekday">{day.weekday}</div>
          {day.has_update ? (
            <div className="status">✅</div>
          ) : day.is_sunday ? (
            <div className="status">🏖️</div>
          ) : (
            <div className="status">❌</div>
          )}
        </div>
      ))}
    </div>
  );
};
```

### Trends Chart Example (with Chart.js)

```typescript
import { Line } from 'react-chartjs-2';

const TrendsChart = ({ trends }) => {
  const data = {
    labels: trends.map(t => `${t.month_name} ${t.year}`),
    datasets: [
      {
        label: 'Update Foizi',
        data: trends.map(t => t.percentage),
        borderColor: 'rgb(75, 192, 192)',
        backgroundColor: 'rgba(75, 192, 192, 0.2)',
      }
    ]
  };

  const options = {
    scales: {
      y: {
        beginAtZero: true,
        max: 100
      }
    }
  };

  return <Line data={data} options={options} />;
};
```

---

## Error Responses

### 401 Unauthorized
```json
{
  "detail": "Not authenticated"
}
```

### 400 Bad Request
```json
{
  "detail": "Invalid month. Must be between 1 and 12"
}
```

### 404 Not Found
```json
{
  "detail": "User not found"
}
```

---

## Notes

1. **Yakshanba kunlari:** Barcha hisob-kitoblarda yakshanba kunlari hisobga olinmaydi (dam olish kuni)
2. **Working days:** Faqat ish kunlari (dushanba-shanba) hisoblanadi
3. **Percentage:** (update_days / working_days) * 100
4. **AI Summary:** O'zbek tilida tayyorlangan, grade va tavsiyalar bilan
5. **Timezone:** Barcha sanalar UTC formatda

---

## UI/UX Recommendations

### Dashboard Layout

```
┌─────────────────────────────────────────────┐
│  Mening Profilim                            │
│                                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐    │
│  │ Bu hafta│  │ Bu oy   │  │ Jami    │    │
│  │   100%  │  │  74.1%  │  │  45     │    │
│  └─────────┘  └─────────┘  └─────────┘    │
│                                             │
│  ┌──────────────────────────────────────┐  │
│  │  📊 Oylik Trend (Chart)              │  │
│  │  ↗️ Natijalaringiz yaxshilanmoqda   │  │
│  └──────────────────────────────────────┘  │
│                                             │
│  ┌──────────────────────────────────────┐  │
│  │  📅 Dekabr 2025 - Kunlik Kalendar    │  │
│  │  [Calendar Grid]                     │  │
│  └──────────────────────────────────────┘  │
│                                             │
│  ┌──────────────────────────────────────┐  │
│  │  🤖 AI Tahlil                        │  │
│  │  ✅ YAXSHI (74.1%)                   │  │
│  │  Yaxshi ish! Izchil ishlayapsan...   │  │
│  └──────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### Color Scheme

- ✅ Update bergan: `#C6EFCE` (light green)
- ❌ Update bermagan: `#FFEB9C` (light yellow)
- 🏖️ Yakshanba: `#FFC7CE` (light red)
- Grade A'LO: Green (#4CAF50)
- Grade YAXSHI: Light Green (#8BC34A)
- Grade O'RTACHA: Orange (#FF9800)
- Grade PAST: Red (#F44336)

---

## Testing

Postman yoki curl bilan test qilish:

```bash
# 1. Get profile
curl -X GET "http://localhost:8000/update-tracking/my-profile" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 2. Get monthly report
curl -X GET "http://localhost:8000/update-tracking/my-monthly-report?month=12&year=2025" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 3. Get calendar
curl -X GET "http://localhost:8000/update-tracking/my-daily-calendar?month=12&year=2025" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 4. Get trends
curl -X GET "http://localhost:8000/update-tracking/my-trends" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Support

Savollar bo'lsa:
- Backend: Ahmad Ahmadov
- Email: admin@company.uz
- Telegram: @ahmad
