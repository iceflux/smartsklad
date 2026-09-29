import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db import Base, get_db


def _sqlite_unicode_lower(value):
    # PostgreSQL's lower() folds Unicode (Cyrillic etc.); SQLite's built-in lower()
    # is ASCII-only. Emulate PG so ilike searches behave identically in tests.
    return None if value is None else value.lower() if isinstance(value, str) else value
from app.main import create_app

import app.modules.audit.models  # noqa: F401 - регистрация таблиц в Base.metadata
import app.modules.auth.models  # noqa: F401
import app.modules.inventory.models  # noqa: F401
import app.modules.customers.models  # noqa: F401
import app.modules.orders.models  # noqa: F401
import app.modules.settings.models  # noqa: F401
import app.modules.notifications.models  # noqa: F401
import app.modules.backup.models  # noqa: F401
import app.modules.importer.models  # noqa: F401
from app.modules.auth.ratelimit import reset_state
from app.modules.auth.service import ensure_default_roles
from app.modules.settings.service import ensure_default_settings
from app.modules.inventory.service import ensure_default_units


class _EngineTrap:
    def __getattr__(self, name):
        raise AssertionError(f"тест не должен обращаться к app.db.engine.{name}")


@pytest.fixture(autouse=True)
def no_app_engine(monkeypatch, db_engine):
    """Заглушка на engine приложения автоусная, а не в одном тесте.

    Подмены одного SessionLocal недостаточно: запись через engine.connect() её
    обошла бы и ушла в рабочую базу из .env, оставив тест зелёным. Ловша
    нужна каждому тесту, а не только проверке генератора демо-данных: иначе
    первая же новая запись в app.db.engine из чужого модуля уедет в базу
    владельца раньше, чем это заметит структурная проверка исходника.
    """
    monkeypatch.setattr("app.db.SessionLocal",
                        lambda: sessionmaker(bind=db_engine, autoflush=False)())
    monkeypatch.setattr("app.db.engine", _EngineTrap())
    yield


@pytest.fixture(autouse=True)
def clean_ratelimit():
    reset_state()
    yield
    reset_state()


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", lambda dbapi_connection, _: dbapi_connection.create_function(
        "lower", 1, _sqlite_unicode_lower
    ))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as s:
        ensure_default_roles(s)
        ensure_default_settings(s)
        ensure_default_units(s)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with Session() as s:
        yield s


@pytest.fixture
def client(db_engine):
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.state.skip_seed_admin = True
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c