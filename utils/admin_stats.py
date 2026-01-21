"""
Admin Statistics and AI Summary Generator
Har bir user uchun statistika va AI xulosasini yaratadi
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional
from models.admin_models import daily_update_log
from models.user_models import user


async def generate_admin_statistics(session: AsyncSession) -> str:
    """
    Barcha userlar uchun statistika va AI xulosasi yaratadi

    Returns:
        O'zbek tilida formatlangan statistika xabari
    """
    # Hozirgi sana
    today = date.today()

    # Oxirgi 30 kun
    date_30_days_ago = today - timedelta(days=30)

    # Barcha active userlarni olish
    result = await session.execute(
        select(user.c.id, user.c.name, user.c.surname, user.c.telegram_id)
        .where(user.c.is_active == True)
        .order_by(user.c.name)
    )
    users = result.fetchall()

    if not users:
        return "❌ Faol foydalanuvchilar topilmadi."

    # Statistika yaratish
    stats_list = []

    for u in users:
        # User uchun oxirgi 30 kundagi yangilanishlar
        updates_result = await session.execute(
            select(daily_update_log)
            .where(
                and_(
                    daily_update_log.c.user_id == u.id,
                    daily_update_log.c.update_date >= date_30_days_ago,
                    daily_update_log.c.update_date <= today
                )
            )
            .order_by(desc(daily_update_log.c.update_date))
        )
        updates = updates_result.fetchall()

        # Statistika
        total_days = 30
        update_days = len(updates)
        update_percentage = round((update_days / total_days) * 100, 1)

        # Oxirgi update
        last_update_date = updates[0].update_date if updates else None
        last_update_content = updates[0].update_content if updates else None

        # Days since last update
        days_since_last = None
        if last_update_date:
            days_since_last = (today - last_update_date).days

        # User info
        full_name = f"{u.name} {u.surname}"
        telegram_username = u.telegram_id or "N/A"

        # AI Summary yaratish
        ai_summary = generate_ai_summary(
            full_name=full_name,
            update_days=update_days,
            total_days=total_days,
            update_percentage=update_percentage,
            last_update_content=last_update_content,
            days_since_last=days_since_last
        )

        stats_list.append({
            'name': full_name,
            'username': telegram_username,
            'update_days': update_days,
            'total_days': total_days,
            'percentage': update_percentage,
            'last_update': last_update_date,
            'days_since_last': days_since_last,
            'ai_summary': ai_summary
        })

    # Formatlangan xabar yaratish
    message = format_admin_report(stats_list, today)

    return message


def generate_ai_summary(
    full_name: str,
    update_days: int,
    total_days: int,
    update_percentage: float,
    last_update_content: Optional[str],
    days_since_last: Optional[int]
) -> str:
    """
    User uchun AI xulosasi yaratadi (o'zbek tilida)

    Bu versiyada oddiy template-based summary.
    Keyinchalik OpenAI/Claude API bilan boyitish mumkin.
    """
    # Baho berish
    if update_percentage >= 90:
        grade = "A'LO"
        emoji = "🌟"
        comment = "Juda yaxshi natija! Doimiy ravishda update bermoqda."
    elif update_percentage >= 75:
        grade = "YAXSHI"
        emoji = "✅"
        comment = "Yaxshi natija! Izchil ishlayapti."
    elif update_percentage >= 50:
        grade = "O'RTACHA"
        emoji = "⚠️"
        comment = "O'rtacha natija. Ko'proq update berish kerak."
    elif update_percentage >= 25:
        grade = "PAST"
        emoji = "❌"
        comment = "Past ko'rsatkich. Update berish kerak!"
    else:
        grade = "JUDA PAST"
        emoji = "🚨"
        comment = "Juda kam update! Darhol e'tibor berish kerak."

    # Oxirgi update haqida
    if days_since_last is not None:
        if days_since_last == 0:
            last_update_info = "Bugun update bergan"
        elif days_since_last == 1:
            last_update_info = "Kecha update bergan"
        elif days_since_last <= 3:
            last_update_info = f"{days_since_last} kun oldin update bergan"
        elif days_since_last <= 7:
            last_update_info = f"{days_since_last} kun oldin update bergan (e'tibor bering!)"
        else:
            last_update_info = f"{days_since_last} kun oldin update bergan (juda uzoq vaqt!)"
    else:
        last_update_info = "Hech qachon update bermagan"

    # Trend tahlili
    if update_percentage >= 75:
        trend = "Faol va izchil ishlamoqda"
    elif update_percentage >= 50:
        trend = "Yaxshiroq natija uchun izchillikni oshirish kerak"
    elif update_percentage >= 25:
        trend = "Update berish chastotasini sezilarli oshirish talab qilinadi"
    else:
        trend = "Darhol rahbariyat e'tiboriga havola qilish kerak"

    # Tavsiya
    if update_percentage >= 90:
        recommendation = "Davom eting! Ajoyib natija."
    elif update_percentage >= 75:
        recommendation = "Yaxshi ish! 100% ga intilish mumkin."
    elif update_percentage >= 50:
        recommendation = "Har kuni update berishga harakat qiling."
    elif update_percentage >= 25:
        recommendation = "Update berish rejasini tuzish tavsiya etiladi."
    else:
        recommendation = "Zudlik bilan rahbariyat bilan gaplashish kerak."

    # Summary yig'ish
    summary = f"""
{emoji} *BAHO: {grade}* ({update_percentage}%)

📊 *Statistika:*
   • {update_days}/{total_days} kun update bergan
   • {last_update_info}

💡 *Tahlil:*
   {comment}
   {trend}

📝 *Tavsiya:*
   {recommendation}
"""

    return summary.strip()


def format_admin_report(stats_list: List[Dict], report_date: date) -> str:
    """
    Admin uchun to'liq hisobotni formatlaydi
    """
    # Sarlavha
    message = f"""
╔════════════════════════════════════════════╗
║         📊 ADMIN STATISTIKA HISOBOTI        ║
╚════════════════════════════════════════════╝

📅 *Sana:* {report_date.strftime('%d.%m.%Y')}
📆 *Davr:* Oxirgi 30 kun
👥 *Xodimlar soni:* {len(stats_list)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""

    # Har bir user uchun statistika
    for i, stat in enumerate(stats_list, 1):
        user_block = f"""
*{i}. {stat['name']}* (@{stat['username']})

📈 *Update foizi:* {stat['percentage']}% ({stat['update_days']}/{stat['total_days']} kun)

{stat['ai_summary']}

{'━' * 44}
"""
        message += user_block

    # Umumiy xulosa
    avg_percentage = sum(s['percentage'] for s in stats_list) / len(stats_list)
    total_updates = sum(s['update_days'] for s in stats_list)

    # Top 3 performerlar
    top_3 = sorted(stats_list, key=lambda x: x['percentage'], reverse=True)[:3]
    bottom_3 = sorted(stats_list, key=lambda x: x['percentage'])[:3]

    message += f"""

╔════════════════════════════════════════════╗
║              🎯 UMUMIY XULOSA              ║
╚════════════════════════════════════════════╝

📊 *O'rtacha ko'rsatkich:* {round(avg_percentage, 1)}%
📝 *Jami updatelar:* {total_updates}
📈 *Kun boshiga o'rtacha:* {round(total_updates / len(stats_list), 1)} kun

🌟 *TOP 3 Faol Xodimlar:*
"""

    for i, stat in enumerate(top_3, 1):
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
        message += f"   {emoji} {stat['name']} - {stat['percentage']}%\n"

    message += f"""
⚠️ *Eng Kam Faol 3 Xodim:*
"""

    for i, stat in enumerate(bottom_3, 1):
        message += f"   {i}. {stat['name']} - {stat['percentage']}%\n"

    message += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 *Tavsiya:*
Kam faol xodimlar bilan individual suhbat o'tkazish tavsiya etiladi.

📞 *Aloqa:* admin@company.uz
"""

    return message
