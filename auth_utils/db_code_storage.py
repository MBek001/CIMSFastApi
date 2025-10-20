from sqlalchemy import select, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from models.user_models import verification_code


class DBCodeStorage:
    """
    Verification va parol tiklash kodlarini saqlash uchun database versiyasi.
    """

    async def set_code(self, session: AsyncSession, user_id: int, code: str, code_type: str) -> bool:
        """
        Yangi kod yaratish yoki mavjud kodni yangilash.
        """
        try:
            # Avval borligini tekshiramiz
            result = await session.execute(
                select(verification_code).where(
                    (verification_code.c.user_id == user_id)
                    & (verification_code.c.type == code_type)
                )
            )
            existing = result.fetchone()

            if existing:
                # Mavjud bo‘lsa, yangilaymiz
                await session.execute(
                    update(verification_code)
                    .where(
                        (verification_code.c.user_id == user_id)
                        & (verification_code.c.type == code_type)
                    )
                    .values(code=code)
                )
                print(f"[DB UPDATE] user_id={user_id} ({code_type}) => {code}")
            else:
                # Yangi yozuv
                await session.execute(
                    insert(verification_code).values(
                        user_id=user_id,
                        code=code,
                        type=code_type
                    )
                )
                print(f"[DB INSERT] user_id={user_id} ({code_type}) => {code}")

            await session.commit()
            return True

        except Exception as e:
            await session.rollback()
            print(f"DBCodeStorage xatolik (set_code): {e}")
            return False


    async def get_code(self, session: AsyncSession, user_id: int, code_type: str) -> str | None:
        try:
            result = await session.execute(
                select(verification_code.c.code).where(
                    (verification_code.c.user_id == user_id)
                    & (verification_code.c.type == code_type)
                )
            )
            record = result.scalar()
            print(f"[DB GET] user_id={user_id} ({code_type}) => {record}")
            return record
        except Exception as e:
            print(f"DBCodeStorage xatolik (get_code): {e}")
            return None


    async def invalidate_code(self, session: AsyncSession, user_id: int, code_type: str) -> bool:
        try:
            await session.execute(
                update(verification_code)
                .where(
                    (verification_code.c.user_id == user_id)
                    & (verification_code.c.type == code_type)
                )
                .values(code="0")
            )
            await session.commit()
            print(f"[DB INVALIDATE] user_id={user_id} ({code_type}) => 0")
            return True
        except Exception as e:
            await session.rollback()
            print(f"DBCodeStorage xatolik (invalidate_code): {e}")
            return False


db_code_storage = DBCodeStorage()
