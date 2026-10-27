from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Colunas adicionadas depois da primeira versao (create_all nao altera tabelas existentes)
_NEW_COLUMNS = {
    "readings": {
        "rssi": "INTEGER",
        "uptime_s": "INTEGER",
        "send_failures": "INTEGER",
        "reasons": "TEXT",
        "feedback": "VARCHAR(16)",
    }
}


def migrate() -> None:
    """Adiciona colunas novas em bancos criados por versoes anteriores."""
    insp = inspect(engine)
    for table, cols in _NEW_COLUMNS.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        with engine.begin() as conn:
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
