"""
Interactive CLI script to create a CEO user in the database.

Usage (inside the running web container):
    docker compose exec -it web python create_ceo.py
or:
    docker exec -it <PROJECT_NAME>_fastapi python create_ceo.py
"""

import asyncio
import getpass
import re
import sys

from sqlalchemy import select, update

from auth_utils.auth_func import get_password_hash
from database import async_session_maker
from models.user_models import UserRole, user

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ask(prompt: str, required: bool = True, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value or not required:
            return value
        print("  Bu maydon majburiy. / This field is required.")


def ask_email() -> str:
    while True:
        value = ask("Email")
        if EMAIL_RE.match(value):
            return value.lower()
        print("  Email formati noto'g'ri. / Invalid email format.")


def ask_password() -> str:
    while True:
        pw1 = getpass.getpass("Parol / Password: ")
        if len(pw1) < 6:
            print("  Parol kamida 6 belgi bo'lsin. / Min 6 characters.")
            continue
        pw2 = getpass.getpass("Parolni tasdiqlang / Confirm password: ")
        if pw1 != pw2:
            print("  Parollar mos kelmadi. / Passwords do not match.")
            continue
        return pw1


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    value = input(f"{prompt} {suffix}: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "ha"}


async def main() -> None:
    print("=" * 50)
    print("CIMS — CEO yaratish / Create CEO user")
    print("=" * 50)

    email = ask_email()

    async with async_session_maker() as session:
        result = await session.execute(select(user).where(user.c.email == email))
        existing = result.mappings().first()

        if existing:
            print(f"\nBu email allaqachon mavjud (id={existing['id']}, "
                  f"role={existing['role']}). / User already exists.")
            if not ask_yes_no("Uni CEO ga ko'tarish? / Promote to CEO?"):
                print("Bekor qilindi. / Aborted.")
                return
            values = {
                "role": UserRole.CEO,
                "role_name": "CEO",
                "is_active": True,
                "is_admin": True,
                "is_staff": True,
                "is_superuser": True,
            }
            if ask_yes_no("Parolni ham yangilash? / Also reset password?"):
                values["password"] = get_password_hash(ask_password())
            await session.execute(update(user).where(user.c.id == existing["id"]).values(**values))
            await session.commit()
            print(f"\n✅ {email} endi CEO. / User promoted to CEO.")
            return

        name = ask("Ism / Name")
        surname = ask("Familiya / Surname")
        password = ask_password()
        job_title = ask("Lavozim / Job title", required=False, default="CEO")
        telegram_id = ask("Telegram ID (ixtiyoriy / optional)", required=False)

        await session.execute(
            user.insert().values(
                email=email,
                name=name,
                surname=surname,
                password=get_password_hash(password),
                role=UserRole.CEO,
                role_name="CEO",
                job_title=job_title or "CEO",
                telegram_id=telegram_id or None,
                company_code="oddiy",
                is_active=True,
                is_admin=True,
                is_staff=True,
                is_superuser=True,
            )
        )
        await session.commit()
        print(f"\n✅ CEO yaratildi: {email} / CEO created successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBekor qilindi. / Aborted.")
        sys.exit(1)
