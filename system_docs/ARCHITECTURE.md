# Архитектура «СмартСклад»

## Общее

- **Модульный монолит**: веб-приложение на FastAPI, развёрнутое как единый сервис.
- Код модулей лежит в `app/modules/{name}/` — каждый модуль содержит свои модели и сервисы внутри своей папки; границы модулей изолированы.
- Точка входа — `app/main.py`: фабрика `create_app()` собирает приложение, монтирует статику и шаблоны Jinja2.
- Конфигурация — `app/config.py` на `pydantic-settings`, значения по умолчанию переопределяются из `.env`.

## База данных

- ORM — **sync SQLAlchemy 2.0** (`DeclarativeBase`, `sessionmaker`): просто для MVP, нет накладных расходов async.
- Движок и фабрика сессий — `app/db.py`; зависимости FastAPI получают сессию через `get_db()`.
- Dev-БД — **PostgreSQL**, миграции управляются **Alembic** (`alembic/`, `alembic.ini`).
- Модели регистрируются в общий `Base.metadata` импортом внутри модулей.

## Тесты

- `tests/` на **pytest** + `httpx` (`TestClient`).
- БД в тестах — **SQLite in-memory** (`StaticPool`), таблицы создаются из `Base.metadata` в `tests/conftest.py` — `pytest` запускается одной командой, Docker не нужен.
- Сейчас: 13 тестов (health, audit, auth models/service/api, password recovery).

## Аутентификация (`app/modules/auth/`)

- Роли — фиксированный enum `Role` (admin, seller, warehouseman, manager).
- Сессии — запись в БД (`sessions`), токен в cookie `session` (httpOnly, samesite=lax). JWT нет.
- `service.py`: bcrypt-хэши, логин/логаут, создание сессии (запись в `audit_log`), восстановление пароля по токену, сброс пароля админом.
- `dependencies.py`: `get_current_user` (401), `require_roles` (403).
- Seed админа: `create_admin_if_empty()` в lifespan при старте, если БД пуста и заданы `ADMIN_USERNAME`/`ADMIN_PASSWORD`.

## Уведомления (`app/modules/notifications/`)

- `email.py`: `send_email(to, subject, body)` на stdlib `smtplib` — без SMTP-зависимости, отправляет через `SMTP_HOST`; при пустом хосте молча пропускает (дизайн «письма не обязательны»).
- Используется модулем auth для отправки ссылки восстановления пароля.

## Зависимости и окружение

- `requirements.txt` — prod-зависимости, `requirements-dev.txt` — dev (-r requirements.txt, pytest, httpx).
- CI (GitHub Actions) — `.github/workflows/test.yml`: install dev-deps → pytest (Python 3.11).