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
- Сейчас: 120 тестов (health, audit, auth models/service/api, roles/users API+UI, password recovery, inventory service/API/UI, audit UI, index page, customers service/API/UI).

## Аутентификация и роли (`app/modules/auth/`)

- Роли — таблица `roles` (name unique, permissions JSON, is_super, is_system) вместо фиксированного enum; `User.role_id` — FK.
- Права — enum `Permission` (17 кодов) в `auth/permissions.py`, хранятся как строки в `roles.permissions`.
- Роль admin — суперроль (`is_super`): все права всегда, независимо от списка прав.
- Системные роли: admin (is_super), seller, warehouseman, manager — права редактируются в UI; кастомные роли создаются через UI.
- Сессии — запись в БД (`sessions`), токен в cookie `session` (httpOnly, samesite=lax). JWT нет.
- `service.py`: bcrypt-хэши, логин/логаут, создание сессии (запись в `audit_log`), восстановление пароля по токену, сброс пароля админом.
- `dependencies.py`: `get_current_user` (401), `require_permissions(*perms)` (403), `ensure_permissions`, `user_has_permission`, `get_role_by_name`, `page_context(user)` (флаги прав для шаблонов), `is_super`.
- API: `/api/roles` (CRUD, справка по правам), `/api/users` (смена роли). Page: `/login`, `/roles`, `/roles/{id}/edit`, `/users` (303→`/login` анониму, 403 без права).
- Seed админа: `create_admin_if_empty()` в lifespan при старте, если БД пуста и заданы `ADMIN_USERNAME`/`ADMIN_PASSWORD`.

## Инвентарь (`app/modules/inventory/`)

- Модели: `Category` (id, name, parent_id → дерево, is_deleted), `Product` (name, sku unique, category_id FK, unit, price, characteristic JSON, rest, is_deleted), `PriceHistory` (product_id, old_price, new_price, changed_by FK users).
- `service.py` (HTTP-agnostic, конфликты → `ValueError`): CRUD категорий и товаров, `get_category_tree` (2 уровня), поиск по name/sku (ILIKE), пагинация, мягкое удаление/восстановление, массовая смена категории, экспорт CSV (BOM + `;`), запись `PriceHistory` при смене цены.
- API (`/api`, права по-полям): категории GET (products_view), POST/PATCH/DELETE (categories_manage); товары — GET products_view, POST products_edit (+stock_change при `rest` в теле), PATCH по-полям (`rest`→stock_change, `price`→price_change, остальное→products_edit, несколько полей = AND), DELETE/restore/bulk — products_edit, export.csv — reports_export. 403 — прав нет, 404 — не найдено, 409 — конфликт (duplicate/self-parent/непустая категория).
- Page: `/products`, `/products/new`, `/products/{id}`, `/products/{id}/edit`, `/categories`, `/categories/new`, `/categories/{id}/edit`; действия — 303-редиректы с `?msg=`.
- Аудит каждого изменяющего действия: `category.create/update/delete`, `product.create/update/delete/restore/bulk_category`.

## Клиенты (`app/modules/customers/`)

- Модель `Customer`: id, name, inn `String(12)` — обычный индекс (не unique), phone, email, discount_card, personal_discount `Float` 0..99, comment `Text` nullable, is_deleted, created_at/updated_at.
- `service.py` (HTTP-agnostic, конфликты → `ValueError`): CRUD + мягкое удаление/восстановление; мягкая валидация — имя обязательное, ИНН ровно 10 цифр если задан (12 — ИНН физлица, не храним; пустое/неуказанное допустимо), скидка 0..99. `list_customers` — поиск ILIKE по name/phone/inn регистронезависимо, пагинация, параметр include_deleted (по умолчанию удалённые скрыты).
- API (`/api/customers`, всё под правом `customers_manage`): GET список (search/include_deleted/page/page_size), POST (201), GET by id, PATCH по-полям (422 при пустом имени/кривом ИНН/скидке вне 0..99), DELETE (204, мягкое), POST `.../restore`. 403 — прав нет, 404 — не найден, 422 — ошибка валидации.
- Page: `/customers`, `/customers/new`, `/customers/{id}`, `/customers/{id}/edit`; delete/restore — POST-действия → 303-редиректы с `?msg=`; форма редактирования на JS отправляет в `/api/customers`.
- Аудит: `customer.create/update/delete/restore`.
- Статистика и история заказов клиента — Спринт 5 (модуль orders).

## Audit UI (`app/modules/audit/`)

- `log_action(db, *, action, entity_type, entity_id, old_value, new_value, user_id, ip)` — keyword-only, коммитит сама.
- `list_audit_logs(db, user_id, action, entity_type, from_, to_, page, page_size)` — фильтры SQL AND, возвращает `(rows, total)`.
- Page `/audit` (audit_view): фильтры, таблица (время/пользователь/действие/сущность/old→new/IP), prev/next пагинация, page_size=50.

## Навигация

- `base.html`: ссылки Товары/Категории/Роли/Пользователи/Журнал аудита, видимость по флагам прав из `page_context(user)` (в `auth/dependencies.py`), передаются через контекст каждого page-роута.

## Уведомления (`app/modules/notifications/`)

- `email.py`: `send_email(to, subject, body)` на stdlib `smtplib` — без SMTP-зависимости, отправляет через `SMTP_HOST`; при пустом хосте молча пропускает (дизайн «письма не обязательны»).
- Используется модулем auth для отправки ссылки восстановления пароля.

## Зависимости и окружение

- `requirements.txt` — prod-зависимости, `requirements-dev.txt` — dev (-r requirements.txt, pytest, httpx).
- CI (GitHub Actions) — `.github/workflows/test.yml`: install dev-deps → pytest (Python 3.11).