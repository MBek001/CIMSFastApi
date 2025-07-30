from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from config import DB_NAME,DB_USER,DB_PASSWORD,DB_HOST,DB_PORT
from sqlalchemy.orm import sessionmaker

from sqlalchemy.ext.declarative import declarative_base
import os


from config import DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
DATABASE_URL = f'postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'


engine = create_async_engine(DATABASE_URL)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=True)

Base = declarative_base()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session