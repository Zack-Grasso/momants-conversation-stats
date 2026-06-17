import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


class WeeklyBase(DeclarativeBase):
    pass


def ensure_weekly_database_exists() -> None:
    """Create weekly_reports on Postgres when the volume predates init-weekly-db.sh."""
    settings = get_settings()
    if settings.weekly_database_url.startswith("sqlite"):
        return

    url = make_url(settings.weekly_database_url)
    db_name = url.database
    if not db_name:
        return

    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                logger.info("Created weekly database %s", db_name)
    finally:
        admin_engine.dispose()


_settings = get_settings()
_engine_kwargs: dict = {"pool_pre_ping": True}
if not _settings.weekly_database_url.startswith("sqlite"):
    _engine_kwargs["pool_size"] = 5
    _engine_kwargs["max_overflow"] = 5
weekly_engine = create_engine(_settings.weekly_database_url, **_engine_kwargs)
WeeklySessionLocal = sessionmaker(bind=weekly_engine, autocommit=False, autoflush=False)


def get_weekly_db() -> Generator[Session, None, None]:
    db = WeeklySessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_weekly_db() -> None:
    from app.weekly import models  # noqa: F401

    ensure_weekly_database_exists()
    WeeklyBase.metadata.create_all(bind=weekly_engine)
    logger.info("Weekly database schema ensured")
