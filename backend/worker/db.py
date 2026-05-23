from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
_engine = create_engine(_sync_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
_SessionLocal = sessionmaker(_engine, expire_on_commit=False)


@contextmanager
def get_session() -> Session:
    with _SessionLocal() as session:
        yield session
