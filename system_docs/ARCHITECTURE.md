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
- Сейчас: 975 тестов (health, audit, auth models/service/api, roles/users API+UI, password recovery, inventory service/API/UI, units, audit UI, index page, сайдбар + node-харнесс, customers service/API/UI, orders service/API/UI, settings service/API/UI, printing service/API, notifications service/API/UI + PDF-вложение, AI service/API/UI, dashboard service/API/UI, backup, importer + необязательные price/rest, генератор демо-данных, удаление категорий + node-харнесс, офлайн-Tailwind + шов меню разметка↔CSS, ограничение частоты входа + текст 429 на странице входа, стоп-guard семантических токенов, горизонтальная прокрутка таблиц на узком экране). Один пропуск — `test_rejects_symlink_pointing_outside_folder`: на Windows без права `SeCreateSymbolicLink` он скипается. node-харнессы `tests/test_categories_page_js.py`, `tests/test_login_page_js.py` и `tests/test_sidebar_script_js.py` пропускаются без node, то есть без node будет 967 passed, 4 skipped. Известный флейк: `tests/test_backup_service.py::test_create_backup_serializes_concurrent_name_reservation` (подробности в корневом `AGENTS.md`, раздел «Тесты»).

## Генератор демо-данных (`scripts/gen_demo.py`)

- Скрипт вне `app/`: приложение его не импортирует, а он импортирует `app.modules.inventory.service` ради константы `DEFAULT_UNITS` и ставит корень репозитория в `sys.path` — поэтому запускается интерпретатором приложения из корня. Зависимостей нет, кроме stdlib.
- Пишет `.json` ровно того формата, который читает `read_payloads` (разделы `products`/`customers`/`orders`), поэтому демо идёт через тот же `/import`, что и настоящая выгрузка 1С; отдельного пути импорта не появляется.
- CLI: `--products 200 --customers 60 --orders 400 --out PATH --seed 1`. Один `random.Random(seed)` на весь файл, поэтому один seed даёт байт в байт одинаковый файл (в течение дня: даты заказов отсчитываются от `date.today()`).
- Единицы берутся из `DEFAULT_UNITS`, а не выдумываются: любая единица вне справочника остановила бы импорт экраном решения, и демо не запустилось бы с первого раза.
- **Базу не трогает** — ни одного запроса; запись в неё принадлежит исключительно экрану импорта. Это закреплено автоусной фикстурой `no_app_engine` в `tests/conftest.py`: она кладёт заглушку и в `app.db.SessionLocal`, и в `app.db.engine`, и падает на любом обращении или вызове, — ставится каждому тесту набора, а не одному, потому что иначе первая же новая запись из чужого модуля уехала бы в рабочую базу из `.env` раньше, чем это заметит структурная проверка исходника. Ловится только обращение по атрибуту: три модуля связывают эти имена при импорте и ловшу обходят — `app/main.py:18`, `app/modules/backup/service.py:15` (его `perform_restore` зовёт `SessionLocal()` на строке 290) и `app/modules/backup/scheduler.py:4`.
- Товары получают `price` и `rest`, а импорт их читает — без этого новый заказ на демо-товаре отклонялся бы проверкой остатка. Механизм чтения — откат `_price`/`_rest` к нулю, а не дефолты `ProductPlan` (они недостижимы). Откат шире, чем кажется: `float("NaN")` и `float("inf")` не бросают исключение, поэтому без `math.isfinite` PostgreSQL принял бы `NaN` в `double precision NOT NULL` молча, и отрицательные цена/остаток прошли бы тем же путём.
- Имена товаров и клиентов дедуплицируются на генерации (`if name in seen`, при совпадении добавляется `№<индекс>`): без этого гварда при дефолтном размере 200/60 импорт схлопывает часть товаров в `matched`, потому что сопоставление идёт по названию. Среднее число коллизий при снятом гварде — 6.95 на n=200 (min 3, max 11) и 2491.0 на n=5000 (min 2449, max 2517) по 20 seed'ам; ожидание по парадоксу дней рождения при 3168 возможных именах — 6.2 и 2485.5, то есть цифры сходятся. Числа получаются так: берётся **тот же порядок обращений к `random.Random(seed)`**, что в `_products` (`scripts/gen_demo.py:107-125`), то есть на каждую итерацию три `randrange` на имя плюс `randrange` на единицу, `uniform` на цену и `randrange` на остаток, — и просто считается, сколько имён повторилось. Считать только `randrange` на имя нельзя: сдвиг потока даёт другой ответ (5.0 и 2479.2), и это уже уводило в отчёт дважды. Ни один из 200 seed'ов при n=200 не даёт нуля, поэтому гвард закреплён тестом на дефолтном размере.

## Аутентификация и роли (`app/modules/auth/`)

- Роли — таблица `roles` (name unique, permissions JSON, is_super, is_system) вместо фиксированного enum; `User.role_id` — FK.
- Права — enum `Permission` (17 кодов) в `auth/permissions.py`, хранятся как строки в `roles.permissions`.
- Роль admin — суперроль (`is_super`): все права всегда, независимо от списка прав.
- Системные роли: admin (is_super), seller, warehouseman, manager — права редактируются в UI; кастомные роли создаются через UI. Права по заказам (`orders.*`): seller — view/create/edit/status (без delete), warehouseman — view/status, manager — view/create/edit/delete/status, admin — всё.
- Сессии — запись в БД (`sessions`), токен в cookie `session` (httpOnly, samesite=lax). JWT нет.
- `service.py`: bcrypt-хэши, логин/логаут, создание сессии (запись в `audit_log`), восстановление пароля по токену, сброс пароля админом, CRUD пользователей (`create_user`, `update_user`) и `generate_password`.
- CRUD пользователей HTTP-agnostic: конфликты и права — через подклассы `ValueError` (`UserConflictError` → 409, `UserPermissionError` → 403), остальная валидация — `ValueError` → 422. `update_user` сначала валидирует все поля, потом применяет изменения (`pending`-dict), поэтому при ошибке частичной мутации нет. Удаления нет — только деактивация (`is_active`).
- `dependencies.py`: `get_current_user` (401), `require_permissions(*perms)` (403), `ensure_permissions`, `user_has_permission`, `get_role_by_name`, `page_context(user)` (флаги прав для шаблонов), `is_super`.
- API: `/api/roles` (CRUD, справка по правам), `/api/users` (GET/PATCH/создание). Page: `/login`, `/roles`, `/roles/{id}/edit`, `/users` (303→`/login` анониму, 403 без права), `/users/new`, `/users/{id}/edit` — формы правятся через JSON API из JS.
- Seed админа: `create_admin_if_empty()` в lifespan при старте, если БД пуста и заданы `ADMIN_USERNAME`/`ADMIN_PASSWORD`.

## Инвентарь (`app/modules/inventory/`)

- Модели: `Category` (id, name, parent_id → дерево, is_deleted), `Product` (name, sku unique, category_id FK, unit_id FK → `Unit`, price, characteristic JSON, rest, `min_stock` Integer NULL, `track_rest` bool NOT NULL default True, is_deleted; `unit` — read-only property от `unit_rel`), `Unit` (name `String(32)` unique среди неудалённых без учёта регистра и пробелов, is_deleted), `PriceHistory` (product_id, old_price, new_price, changed_by FK users).
- `track_rest` фиксируется при создании товара: `create_product(..., *, track_rest: bool | None = None)` — `None` берёт глобальную настройку `stock_track_default` (`!= "false"`); глобальная настройка влияет только на новые товары. `update_product` принимает `track_rest` (право products_edit).
- `service.py` (HTTP-agnostic, конфликты → `ValueError`): CRUD категорий и товаров, `get_category_tree` (2 уровня), поиск по name/sku (ILIKE), пагинация, мягкое удаление/восстановление, массовая смена категории, экспорт CSV (BOM + `;`), запись `PriceHistory` при смене цены.
- API (`/api`, права по-полям): категории GET (products_view), POST/PATCH/DELETE (categories_manage); товары — GET products_view, POST products_edit (+stock_change при `rest` в теле), PATCH по-полям (`rest`→stock_change, `price`→price_change, остальное→products_edit, несколько полей = AND), DELETE/restore/bulk — products_edit, export.csv — reports_export. 403 — прав нет, 404 — не найдено, 409 — конфликт (duplicate/self-parent/непустая категория).
- Page: `/products`, `/products/new`, `/products/{id}`, `/products/{id}/edit`, `/categories`, `/categories/new`, `/categories/{id}/edit`; действия — 303-редиректы с `?msg=`.
- `delete_category(db, id, user=None, *, resolve=False, reassign_to=None)`: содержимое считается `category_contents` **только по неудалённым** товарам и подкатегориям; непустая категория без `resolve` отказывает `ValueError` с русским сообщением, называющим, что именно мешает (формы множественного числа даёт `_plural`) → 409. `resolve=true` + `reassign_to=<id>` переносит товары и подкатегории в адресата, `resolve=true` без адресата снимает категорию в `NULL`; `resolve` — отдельный флаг именно потому, что «без категории» и «параметр не передан» — это один и тот же `reassign_to=None`. Перенос в саму себя или неудалённого потомка отклоняется (`_descendant_ids`), удалённая категория целью быть не может. Мягко удалённые строки не переносятся и удаление не блокируют. UI предлагает адресата списком с номерами из `{{ tree | tojson }}` и шлёт `DELETE /api/categories/{id}?resolve=true[&reassign_to=]`; правописание множественного числа продублировано в JS страницы как `plural` — клиент и сервер код не делят.
- Аудит каждого изменяющего действия: `category.create/update/delete`, `product.create/update/delete/restore/bulk_category`.

## Клиенты (`app/modules/customers/`)

- Модель `Customer`: id, name, inn `String(12)` — обычный индекс (не unique), phone, email, discount_card, personal_discount `Float` 0..99, comment `Text` nullable, is_deleted, created_at/updated_at.
- `service.py` (HTTP-agnostic, конфликты → `ValueError`): CRUD + мягкое удаление/восстановление; мягкая валидация — имя обязательное, ИНН ровно 10 цифр если задан (12 — ИНН физлица, не храним; пустое/неуказанное допустимо), скидка 0..99. `list_customers` — поиск ILIKE по name/phone/inn регистронезависимо, пагинация, параметр include_deleted (по умолчанию удалённые скрыты).
- API (`/api/customers`, всё под правом `customers_manage`): GET список (search/include_deleted/page/page_size), POST (201), GET by id, PATCH по-полям (422 при пустом имени/кривом ИНН/скидке вне 0..99), DELETE (204, мягкое), POST `.../restore`. 403 — прав нет, 404 — не найден, 422 — ошибка валидации.
- Page: `/customers`, `/customers/new`, `/customers/{id}`, `/customers/{id}/edit`; delete/restore — POST-действия → 303-редиректы с `?msg=`; форма редактирования на JS отправляет в `/api/customers`.
- Аудит: `customer.create/update/delete/restore`.
- Статистика и история заказов клиента — модуль orders (раздел ниже).

## Заказы (`app/modules/orders/`)

- Модели: `Order` (id, number `String(16)`, customer_id FK, status `String(16)`, discount `Float`, total_amount, created_at/updated_at, deleted_at), `OrderItem` (product_id FK, quantity, снимки product_name_snapshot/product_price_snapshot/discount_snapshot/unit_snapshot).
- Статусы: `new`, `processing`, `done`, `cancelled`. Переходы (`NEXT_POSSIBLE`): new→{processing,cancelled}, processing→{done,cancelled}, done→{processing,cancelled}; смена статуса «на тот же» или невалидный переход — `ValueError`. При переводе в `done` списываются остатки (`_check_and_writeoff` — две фазы: сначала проверка всех позиций, потом списание), при `done`→другой статус — возврат (`_return_stock`).
- Скидки только в процентах, 0..99 (`_validate_discount`, деньги/рубли не поддерживаются). Итог строки `qty*price*(1-disc/100)`, заказа `сумма*(1-order.discount/100)`, `round(2)` (`recalc_total`). Дефолт скидки заказа при создании — `customer.personal_discount`.
- Клиент обязателен и не мягко удалён (`_get_customer`) — единственное исключение: в `update_order` приведённый ниже клиент не трогается, если не изменился.
- Проверка остатка при создании и редактировании (`_validate_items`) и при переводе в `done`; редактировать выполненный заказ нельзя (service + UI-редирект).
- Товары с `track_rest=False` полностью исключены из учёта остатков в заказах: в `_validate_items` пропускается проверка остатка (проверка `qty>=1` остаётся всегда), в `_check_and_writeoff` — проверка и списание, в `_return_stock` — возврат.
- Номер заказа `00001`-стиль: номер последнего по `max(id)` заказа плюс один (`next_order_number`), уникальность не гарантируется (no unique constraint) — известное ограничение MVP.
- Мягкое удаление `deleted_at` + restore; copy — новый заказ `new`, позиции копируются из живых снимок исходного заказа: `copy_order` кладёт в спец-ключи `price_snapshot`/`unit_snapshot` (`orders/service.py:235-241`), а `create_order` читает `spec.get("price_snapshot", product.price)` / `spec.get("unit_snapshot", product.unit)` (`:93,96`) — без ключей поведение прежнее (значения карточки), поэтому API и форма не затронуты. Практическое следствие: копия заказа из 1С сохраняет настоящие цены продажи, а не нули из карточек.
- Порядок важен и для копии: `create_order` зовёт `_validate_items` (`:87`) раньше построения позиций, поэтому в состоянии после импорта (`rest=0`, `track_rest=True`) копирование отклоняется по остатку. Это то же ограничение, что у новых заказов на импортированные товары. Страница копирования (`app/main.py:1483-1489`) отделяет 404 **по типу** — `OrderNotFoundError` и `CustomerNotFoundError`, оба подкласса `ValueError` (`orders/service.py:20-25`); тем идут 303 с `?msg=`, как на `orders_edit_post`. Имена исключений во всём проекте с суффиксом `Error` (`UserConflictError`, `UserPermissionError`, `RoleNotFoundError` в `auth`, `ImportFileError` в `importer`), «не найдено» — не исключение из правила. Сравнений строк-маркеров в коде не осталось ни одного: раньше они превращали «insufficient stock …» в «Заказ не найден», а с переводом текстов на русский такой механизм тихо превратил бы каждый 404 в обычное сообщение. Все сообщения сервиса заказов русские.
- `update_order` не перезаписывает `product_price_snapshot`/`unit_snapshot` у позиций, которые не меняли: снимки старых строк читаются **до** `items.clear()` (иначе refresh expung'нутой строки) и раздаются новым строкам из очереди, ключ — `product_id`, а не номер строки (форму можно чистить из середины; повторяющийся товар забирает снимки по очереди). Товар, добавленный заново, получает цену и единицу из карточки. Практическое следствие: правка цены в карточке не меняет цену уже существующей позиции, а у заказов из 1С (`price=0` в карточке) снимок сохраняет реальную цену продажи.
- Тот же приём применён к клиенту: `update_order` трогает клиента только когда он **меняется** — `if customer_id is not None and customer_id != order.customer_id` (`orders/service.py:177`), то есть не-равенство, а равенство пропускает всё, включая проверку. Иначе форма редактирования была бы неотправляемой — текущий удалённый клиент показывается в списке с пометкой «— удалён» (см. ниже про опции формы), но `_get_customer` его отвергает, а page-роут отдаёт 404. Смена клиента на другого удалённого или на несуществующий по-прежнему отказ; `create_order` и `copy_order` правила не касаются — там удалённый клиент отказ всегда.
- `service.py` (HTTP-agnostic, конфликты → `ValueError` или его подклассы `OrderNotFoundError`/`CustomerNotFoundError`): create/update/delete/restore/copy/change_status/list/get, `customer_order_stats` (orders_count/total_sum/avg_check среди не-cancelled, не удалённых).
- API (`/api/orders`, права): GET/POST — view/create, GET/PATCH/DELETE `/orders/{id}` — view/edit/delete, POST `.../restore` — delete, `.../copy` (201) — create, `.../status` — status. 403 — прав нет, 404 — не найдено, 422 — ошибка валидации.
- Page: `/orders` (фильтры search/status/customer/date/min_amount/include_deleted, пагинация), `/orders/new`, `/orders/{id}` (позиции + история изменений — последние 20 `AuditLog` с `entity_type="order"`), `/orders/{id}/edit`; POST-действия → 303-редиректы с `?msg=`. На `/customers/{id}` — блок «Заказы клиента» со статистикой.
- Опции формы редактирования (`_client_options`/`_product_options` в `app/main.py`) — это `list_customers`/`list_products` без `page_size`, то есть первые 25 по имени, плюс текущие значения заказа, дописанные по `id` через `bisect.insort`; мягко удалённые текущие значения получают пометку `deleted` и в шаблоне печатаются с текстом «— удалён». Атрибут `disabled` не ставится намеренно: у `select` с отключённой выбранной опцией в форму не приходит ничего, а `_order_form_items` склеивает `product_id`/`quantity`/`line_discount` по позиции, и строки бы разъехались. Поиска товара в форме нет — в новую строку попадает только то, что есть в списке; это известное ограничение.
- Аудит: `order.create/update/delete/restore/status_change` (`entity_type="order"`).

## Настройки (`app/modules/settings/`)

- Модель `Setting` (таблица `settings`: id, key unique, value, updated_at). Ключ `stock_track_default` со значением `"true"`/`"false"`, сидится `ensure_default_settings`.
- `service.py`: `get_setting(db, key) -> str | None`, `set_setting(db, key, value, *, user=None)` — аудит `settings.update`.
- API: `GET /api/settings` — список `[{key, value}, ...]` для любого аутентифицированного (без права); `PUT /api/settings/stock-track` — требует `settings.change`, значение строго `"true"`/`"false"` иначе 422.
- Page `/settings` (GET/POST → 303 + `?msg=`), гейт `settings.change` (аноним → 303 `/login`, без права → 403); ссылка «Настройки» в `base.html` скрыта без `can_change_settings` из `page_context`.
- `Product.track_rest` (см. Инвентарь): глобальная настройка задаёт дефолт только для новых товаров.

## Прогноз спроса (`app/modules/ai/`)

- Модуль без моделей и миграций: прогноз — производная величина, считается на лету при запросе (кэша и сохранённых моделей нет).
- Источник данных: позиции заказов со `status='done'` и `deleted_at IS NULL`, окно 90 дней; плотный ряд дневных продаж (дни без продаж = 0). Товары в выборке — `is_deleted=False` и `track_rest=True`.
- Метод: линейная регрессия по методу наименьших квадратов на stdlib; новых зависимостей нет. Порог данных — 14 дней с ненулевыми продажами, иначе прогнозы и рекомендация `None`.
- Горизонты 7/14/30 дней. Правило закупки: `need = прогноз_30 + min_stock − rest`, рекомендация при `need > 0`.
- Порог запаска: `Product.min_stock` (штуки) как переопределение, иначе `round(средние_дневные_продажи × min_stock_days)` из настройки `min_stock_days` (дефолт 14, диапазон 0-365). Настройка **обязательна** в `POST /settings`: отсутствие поля отклоняется, и тогда не сохраняется ничего — это защита от неполного POST, который иначе молча сбросил бы сохранённое значение на дефолт.
- `min_stock` отклоняется в отрицательную сторону и при создании, и при правке (в `update_product` проверка идёт до любых мутаций, поэтому частичной правки не остаётся); значение попадает в снимок аудита `product.update`; пустое поле формы отправляет `null` (снятие переопределения), а не `0`.
- API: `GET /api/forecast` (`only_purchase`, `product_id`, `page`, `page_size`). Page: `/forecast`. Право: `analytics.view` (manager + admin). Аудита нет — только чтение.

## Аналитика продаж (`app/modules/dashboard/`)

- Без моделей и миграций: агрегаты считаются на лету при запросе, кэша и сохранённых отчётов нет.
- Источник: заказы `status='done'` с `deleted_at IS NULL` в выбранном периоде — тот же критерий, что у прогноза спроса и списания остатков. Мягко удалённые клиенты и товары в топах остаются: продажа состоялась.
- Метрики: `orders_count`, `total_sum`, `avg_check` (округление 2, `0.0` при пустом периоде), плотный ряд `daily` (дни без продаж = нули), топ-N товаров по количеству, топ-N клиентов по сумме, где N = `top_limit` (по умолчанию 10, 1..500). Сортировка полностью детерминирована: по значению, затем по имени, затем по `product_id`/`customer_id` — ID нужен как последний ключ, потому что два товара или клиента могут называться одинаково, и без него выбор на границе топа зависел бы от порядка строк в БД.
- Размер топа — параметр запроса, а не глобальная настройка: страница читает `?top_limit` и молча берёт 10 вне диапазона и при не-числе, API объявляет `Query(10, ge=1, le=500)` (вне диапазона — 422). Глобальный ключ `dashboard_top_limit` был заведён и удалён: он давал право `analytics.view` переписывать себя из обычного GET, а API его всё равно игнорировал, так что страница и API расходились.
- Период: `resolve_period(preset, date_from, date_to, *, earliest=None)` — скользящие окна `day`/`week`/`month`/`year` (1/7/30/365 дней, конец — сегодня), календарные `quarter` (с 1-го числа текущего квартала), `prev_quarter` (предыдущий квартал целиком) и `cal_year` (с 1 января), `all` (от `earliest` до сегодня) либо произвольные `date_from`/`date_to`; границы включительные. `year` — это 365 дней, а не календарный год, и на странице он так и подписан. При `date_from > date_to` функция выбрасывает `ValueError`: API и CSV возвращают 422, а `/dashboard` перенаправляет с сообщением. Пустой `preset` (`?preset=`) равнозначен отсутствию параметра и даёт `DEFAULT_PRESET`; неизвестный непустой `preset` падает на `day`. Нормализация живёт в самом `resolve_period`, поэтому все три слоя согласованы.
- `earliest` — keyword-only, поэтому каждый вызывающий передаёт его явно: `dashboard/router.py`, страница `/dashboard` и `/dashboard/export.csv`. Отчёт по email по cadence day/week/month его не передаёт и не нуждается: там периоды только `day`/`week`/`month`.
- Известное ограничение, не чинить на глаз: `earliest_order_date` берёт `min(Order.created_at)` по **всем** заказам, без фильтров `status`/`deleted_at`, которые применяет `_sales_orders`. Из-за этого «Весь период» может начаться раньше первой реальной продажи (в том числе из-за одного старого не-`done`-заказа), а `chart_bars` рисует по прямоугольнику на день и расширяет `viewBox` вместе с диапазоном — «Весь период» с заказом пятилетней давности даст тысячи прямоугольников. Починка на будущее — одна строка: тот же фильтр в `earliest_order_date`.
- График: инлайновый SVG, геометрия в чистой функции `chart_bars`, возвращающей адаптивную ширину — при 365 полосах `viewBox` расширяется, чтобы полосы не обрезались. Функция также отдаёт координаты `left`, `axis_y`, `label_y`, чтобы шаблон не дублировал константы `CHART_LEFT`/`CHART_BOTTOM`/`CHART_LABEL_OFFSET` числами.
- UI-детали: поля дат намеренно оставляются пустыми, потому что `resolve_period` при двух заданных границах игнорирует `preset`; фактический диапазон показывается строкой `Показано`. Ссылка CSV передаёт resolved `date_from`/`date_to`, а не `preset`, чтобы экспорт совпадал с видимым диапазоном. Поле «Строк в топе» — нативный `<input type="number">` с `<datalist>` 5/10/20/50, любое значение вводится руками.
- CSV: `dashboard_sales_csv` — плоская таблица дневных продаж с BOM, разделитель `;` (идиома `export_products_csv`).
- API: `GET /api/dashboard/sales` (`analytics.view`). Страница: `/dashboard` (`analytics.view`) и `/dashboard/export.csv` (`reports.export`). Аудита нет — только чтение.

## Резервное копирование (app/modules/backup/)

- Модуль добавляет две таблицы: `backup_settings` с единственной строкой настроек и `backup_log` с журналом запусков. Они создаются одной миграцией `c3a91f4b7d20` после `7c1d2e3f4a5b`; модель — `app/modules/backup/models.py`. В `backup_settings` хранятся также `last_report_at` для ручной/фактической отправки и `last_report_scheduled_at` для планового запуска.
- `create_backup` запускает `pg_dump --format=custom --no-password` и сначала пишет архив во временный файл `.part`; после успешного завершения файл атомарно переименовывается в `.dump`. Резервирование имени и запись дампа защищены process-local `threading.Lock`; чтение настроек копии (`get_backup_settings`) остаётся **вне** лока — из-за этого два потока с общим DBAPI-коннектом (именно так устроены тесты, `StaticPool`) могут повредить друг другу чтение, см. известный флейк в корневом `AGENTS.md`. Пароль и параметры подключения передаются утилите через окружение, пароль — только через `PGPASSWORD`, никогда через argv.
- Поиск `pg_dump` и `pg_restore` учитывает ручной `backup_settings.pg_dump_path`: при заданном пути `pg_restore` ищется рядом с утилитой, иначе обе утилиты ищутся в `PATH`. Панель заранее показывает пути, major-версии утилиты/сервера и доступность папки.
- Планировщик запускается в lifespan при старте приложения. Он спит между проверками с настраиваемым интервалом `backup_settings.check_interval_minutes` (1..1440 минут), определяет ежедневную задачу копирования, проверяет режим обслуживания и защищает проверку PostgreSQL advisory-lock. Ошибка самого планировщика записывается в `backup_settings.last_error`; результаты задач backup и report фиксируются соответственно в `backup_log` и `notification_log`. Приложение не останавливается.
- Восстановление сначала запускает обратный отсчёт 10..3600 секунд, затем переводит режим обслуживания в `running`. Middleware отвечает 503 на обычные страницы и API, кроме `/static`, точного `/api/backup/status` и маршрута отмены, который в `running` является no-op. Состояние режима хранится в памяти процесса, поэтому для корректного восстановления нужен один процесс.
- `run_restore` использует `pg_restore --clean --if-exists --no-password` в существующей базе, а не `--create`. Восстановление возвращает состояние всей базы из архива, включая сессии, настройки, `backup_settings` и `audit_log`.
- Отчёт по email хранит настройки в `settings` (`report_email_enabled`, `report_email_period`, `report_email_time`, `report_email_recipients`). Период одновременно задаёт частоту (`day`, `week`, `month`) и окно данных. `send_report` переиспользует `dashboard_metrics` и `dashboard_sales_csv`, считает метрики на лету и отправляет отдельное письмо с CSV каждому выбранному пользователю; получатель без email пропускается с записью в `notification_log`.
- Кнопка `/backups/report/send` выполняет один запуск `send_report` без изменения настроек расписания. Ручной запуск обновляет счётчики фактической отправки, а планировщик отдельно обновляет `last_report_scheduled_at`.

## Audit UI (`app/modules/audit/`)

- `log_action(db, *, action, entity_type, entity_id, old_value, new_value, user_id, ip)` — keyword-only, коммитит сама.
- `list_audit_logs(db, user_id, action, entity_type, from_, to_, page, page_size)` — фильтры SQL AND, возвращает `(rows, total)`.
- Page `/audit` (audit_view): фильтры, таблица (время/пользователь/действие/сущность/old→new/IP), prev/next пагинация, page_size=50.

## Навигация

- `base.html`: ссылки Товары/Категории/Клиенты/Заказы/Аналитика/Прогноз/Роли/Пользователи/Журнал аудита/Уведомления/Настройки, видимость по флагам прав из `page_context(user)` (в `auth/dependencies.py`), передаются через контекст каждого page-роута.

## Печать документов (`app/modules/printing/`)

- `service.py` (HTTP-agnostic): `build_order_context(order, db)` — данные заказа из снимков `OrderItem` + имя клиента (пусто, если клиент мягко удалён); `render_order_html(order, db)` → HTML-строка; `render_order_pdf(order, db)` → bytes PDF.
- HTML — Jinja2 `Environment` (свой `JINJA`, `FileSystemLoader` на `app/templates`, `autoescape=True`) + фиксированный шаблон `documents/order_print.html` (номер, клиент, дата, скидка, итог, таблица позиций).
- PDF — ReportLab `SimpleDocTemplate` (A4, `platypus` Table); кириллица через регистрацию TTF (`_register_cyrillic_font`): Arial из `C:/Windows/Fonts`, fallback — DejaVuSans/Vera из пакета reportlab, без найденного шрифта — Helvetica.
- Page-роуты в `app/main.py`: `GET /orders/{id}/print` — HTML-предпросмотр (orders_view; кнопки «Печать»/«Скачать PDF»/«На email»/«В Telegram», `@media print`, `can_send_notifications`); `GET /orders/{id}/pdf` — скачивание PDF (`Content-Disposition: attachment; filename="order-{number}.pdf"`, orders_view).

## Уведомления (`app/modules/notifications/`)

- `email.py`: `send_email(to, subject, body, *, attachment: bytes | None = None, filename: str = "document.pdf", attachment_subtype: str = "pdf")` на stdlib `smtplib` — без SMTP-зависимости, отправляет через `SMTP_HOST`; при пустом хосте молча пропускает (дизайн «письма не обязательны»); `attachment` — файл в MIME-вложении, по умолчанию PDF (`application/pdf`). Используется модулями auth (восстановление пароля), orders (отправка заказа) и backup. Отчёт backup повторно использует `dashboard_sales_csv` и отправляет CSV-вложение через `attachment_subtype="csv"`.
- `service.py`: `message_text(order, db)` — краткое описание заказа; `send_document(db, order, channel, user)` — канал `email`/`telegram`, генерирует PDF `render_order_pdf(order, db)` и шлёт его вложением обоим каналам (имя `order-{number}.pdf`, включается всегда); пишет запись в `notification_log` (status `sent`/`failed` + `error`), сетевые/HTTP-ошибки ловит и логирует, служебные `ValueError` (email не задан, telegram не настроен) пробрасывает; аудит `notification.send`. recipient: email — `customer.email`, telegram — настройка `telegram_chat_id`.
- `telegram.py`: `send_telegram(text, db)` — httpx POST на `https://api.telegram.org/bot{token}/sendMessage` (timeout 10); token/chat_id из настроек `telegram_bot_token`/`telegram_chat_id`, при отсутствии — `ValueError("telegram not configured")`. `send_telegram_document(text, pdf, db, *, token=None, chat_id=None, filename="document.pdf")` — POST на `sendDocument`, текст заказа идёт caption документа.
- `models.py`: `NotificationLog` — таблица `notification_log` (channel, recipient, subject, text_body, document_type/document_id FK→orders, status, error, user_id FK→users, created_at).
- Права: `notifications.send`, `notifications.view` (enum `Permission`) — у admin+manager; seller/warehouseman их не имеют. `page_context`: `can_send_notifications`, `can_view_notifications`.
- API: `POST /api/orders/{id}/send` (body `{channel}`; notifications.send; 404 — заказ, 422 — канал вне email/telegram или не сконфигурирован получатель), `GET /api/notifications` (notifications.view; фильтры channel/status/date_from/date_to, пагинация page/page_size).
- Page: `POST /orders/{id}/send` (кнопки на печатной форме → 303 redirect к `?msg=`), `GET /notifications` — журнал уведомлений (notifications.view); нав-ссылка «Уведомления» по `can_view_notifications`.
- Настройки (модуль settings): ключи `telegram_bot_token`/`telegram_chat_id`, сидятся `ensure_default_settings`; на `/settings` токен маскируется (`первые3+****+последние3`), пустое поле при сохранении токен не перезаписывает.

## Зависимости и окружение

- `requirements.txt` — prod-зависимости, `requirements-dev.txt` — dev (-r requirements.txt, pytest, httpx).
- CI (GitHub Actions) — `.github/workflows/test.yml`: install dev-deps → pytest (Python 3.11).