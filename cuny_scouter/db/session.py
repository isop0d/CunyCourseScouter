from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from cuny_scouter.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
