# utils/currency.py
import os
from datetime import datetime, timedelta
from decimal import Decimal
import httpx
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from models.admin_models import exchange_rate

# --- Tashqi API: CurrencyFreaks ---
API_BASE = "https://api.currencyfreaks.com/v2.0/rates/latest"
CACHE_TTL = timedelta(hours=6)  # agar kerak bo'lsa live getterda ishlatish mumkin
DEFAULT_RATE = Decimal("12700.00")  # fallback

# Agar siz FREECURRENCYAPI_KEY dan foydalangan bo'lsangiz ham,
# hozircha config.py ichida os.environ.get('FREECURRENCYAPI_KEY') ni ishlatishingiz mumkin.
# CurrencyFreaks uchun kalitni ham xuddi shu nom bilan bera olasiz,
# yoki CURRENCYFREAKS_API_KEY nomida bersangiz ham bo'ladi.
try:
    from config import FREECURRENCYAPI_KEY as CONFIG_KEY  # siz so‘ragancha configdan ENV o‘qiladi
except Exception:
    CONFIG_KEY = None

def _read_api_key() -> str | None:
    """
    API keyni config.py dagi FREECURRENCYAPI_KEY dan oladi,
    bo'lmasa CURRENCYFREAKS_API_KEY env'dan oladi.
    """
    if CONFIG_KEY:
        key = str(CONFIG_KEY).strip()
        if key:
            return key
    key = os.environ.get("CURRENCYFREAKS_API_KEY", "") or os.environ.get("FREECURRENCYAPI_KEY", "")
    return key.strip() or None


class CurrencyService:
    """
    Live/sync endpointlari uchun tashqi API'dan USD→UZS kursini olish va DB'ga yozish.
    Qolgan joylar DB'dagi eng so'nggi kursni ishlatadi (get_last_rate_from_db).
    """
    def __init__(self):
        self.api_key = _read_api_key()

    async def fetch_usd_to_uzs(self) -> Decimal:
        """
        CurrencyFreaks dan USD→UZS kursini olib keladi (API chaqiradi).
        """
        if not self.api_key:
            return DEFAULT_RATE

        params = {"apikey": self.api_key}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(API_BASE, params=params)

            if resp.status_code == 401:
                raise ValueError("CurrencyFreaks 401: API key noto‘g‘ri yoki ruxsat yo‘q.")
            if resp.status_code == 403:
                raise ValueError("CurrencyFreaks 403: reja/limit cheklovi.")
            resp.raise_for_status()

            data = resp.json()
            # {"date": "...", "base": "USD", "rates": {"UZS": "#####.##", ...}}
            rate_str = data["rates"]["UZS"]
            return Decimal(rate_str)

    async def write_rate_to_db(self, session: AsyncSession, rate: Decimal) -> None:
        """Yangi kursni DB'ga yozish."""
        await session.execute(
            insert(exchange_rate).values(
                usd_to_uzs=rate,
                updated_at=datetime.utcnow()
            )
        )
        await session.commit()


# --- Barcha biznes-logika uchun DB'dan oxirgi kursni o'qish helperi ---
async def get_last_rate_from_db(session: AsyncSession) -> Decimal:
    """
    DB’dagi eng so‘nggi USD→UZS kursini qaytaradi.
    Yo‘q bo‘lsa DEFAULT_RATE (12700.00) qaytadi.
    Hech qachon tashqi API’ga chiqmaydi.
    """
    res = await session.execute(
        select(exchange_rate.c.usd_to_uzs)
        .order_by(exchange_rate.c.updated_at.desc())
        .limit(1)
    )
    rate = res.scalar()
    return Decimal(str(rate)) if rate is not None else DEFAULT_RATE
