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


class _AppDbTrap:
    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attr):
        raise AssertionError(f"тест не должен обращаться к app.db.{self.name}.{attr}")

    def __call__(self, *args, **kwargs):
        raise AssertionError(f"тест не должен звать app.db.{self.name}()")


@pytest.fixture(autouse=True)
def no_app_engine(monkeypatch):
    """Ловушка на базу приложения автоусная, а не в одном тесте.

    Подмены одного SessionLocal на тестовый движок недостаточно: она и
    перенаправляла бы запись вместо того, чтобы её ловить. Здесь оба атрибута
    `app.db` падают, поэтому первая же новая запись из чужого модуля видна
    сразу, а не уезжает ни в рабочую базу из `.env`, ни в тестовую.

    Ловится только обращение по атрибуту. Три модуля связывают их при импорте
    и потому ловшу обходят: `app/main.py:18`, `app/modules/backup/service.py:15`
    (его `perform_restore` зовёт `SessionLocal()` на строке 290) и
    `app/modules/backup/scheduler.py:4`.
    """
    monkeypatch.setattr("app.db.SessionLocal", _AppDbTrap("SessionLocal"))
    monkeypatch.setattr("app.db.engine", _AppDbTrap("engine"))
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