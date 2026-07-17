"""
One-time script: create ALL database tables from SQLAlchemy metadata.

Usage (inside the running web container):
    docker compose exec -it web python create_tables.py

Safe to re-run — uses CREATE TABLE IF NOT EXISTS semantics (checkfirst=True).
"""

import asyncio

from database import engine

# Import every model module so all tables register on the shared metadata
from models.admin_models import metadata
import models.user_models  # noqa: F401
import models.projects_models  # noqa: F401
import models.instagram_models  # noqa: F401
import cognilabsai.tables  # noqa: F401


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    await engine.dispose()
    print(f"✅ {len(metadata.tables)} ta jadval yaratildi/tekshirildi. / "
          f"{len(metadata.tables)} tables created/verified.")


if __name__ == "__main__":
    asyncio.run(main())
