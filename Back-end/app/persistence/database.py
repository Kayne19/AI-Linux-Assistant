import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from utils.env import load_project_dotenv


class Base(DeclarativeBase):
    pass


def normalize_database_url(database_url):
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]
    parsed = urlparse(database_url)
    if parsed.query:
        cleaned_query = urlencode(
            [
                (key.strip(), value.strip())
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        database_url = urlunparse(parsed._replace(query=cleaned_query))
    return database_url


def get_database_url():
    load_project_dotenv(start_dir=Path(__file__).resolve().parent)
    raw = (os.getenv("DATABASE_URL") or "").strip()
    return normalize_database_url(raw)


def build_engine(database_url=None, echo=False):
    database_url = normalize_database_url(database_url or get_database_url())
    if not database_url:
        raise ValueError("DATABASE_URL is not set.")

    return create_engine(
        database_url,
        future=True,
        echo=echo,
        pool_pre_ping=True,
    )


def build_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@lru_cache(maxsize=1)
def get_engine():
    return build_engine()


@lru_cache(maxsize=1)
def get_session_factory():
    return build_session_factory(get_engine())
