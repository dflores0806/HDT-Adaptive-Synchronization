"""SQLAlchemy engine configuration for the bounded, single-host IoTJ lab.

The pool settings concern *infrastructure capacity*, not the adaptive policy.
They are explicitly controlled via environment variables and identical for all
policies in a campaign. SQLite unit tests retain SQLAlchemy's native defaults.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .config import settings

pool_kwargs = {}
if settings.POSTGRES_DSN.startswith('postgresql'):
    pool_kwargs = {
        'pool_size': int(os.getenv('HDT_DB_POOL_SIZE', '20')),
        'max_overflow': int(os.getenv('HDT_DB_MAX_OVERFLOW', '20')),
        'pool_timeout': float(os.getenv('HDT_DB_POOL_TIMEOUT_S', '5')),
        'pool_pre_ping': True,
    }
    if pool_kwargs['pool_size'] < 1 or pool_kwargs['max_overflow'] < 0 or pool_kwargs['pool_timeout'] <= 0:
        raise ValueError('Invalid SQLAlchemy capacity settings')

engine = create_engine(settings.POSTGRES_DSN, future=True, **pool_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def get_session():
    return SessionLocal()
