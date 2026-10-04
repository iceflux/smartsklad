"""Живая фильтрация не должна уводить фокус из поля «Поиск».

Дефект, который ловит этот тест: страница целиком перерисовывалась на каждое
нажатие, и после первой буквы фокус уходил на `<body>` — дописать слово было
нельзя, поле приходилось нажимать заново.

Проверка поведенческая, в настоящем браузере: `document.activeElement` после
перерисовки, положение каретки и то, что узел поля остался **тем же самым**.
Последнее — решающее: если бы страница переехала целиком, узел был бы новым,
и одного `activeElement` мало — оно указывало бы на поле, но уже чужое.

Фильтрация приходит настоящим ответом сервера (его отдаёт TestClient), подменена
только сеть: из `file://` настоящий `fetch` не ходит, а разбирать HTML должен
настоящий `DOMParser`.

Страниц две, и это не украшение: живая фильтрация привязана к
`input[name=search]`, и страница заказов отдаёт его по тому же `search`, что и
товары. Проверка на товарах доказала бы только свойство товаров.
"""

import html as html_mod
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)

MEASURE = r"""
function type(value) {
  var field = document.querySelector('form[data-live-filter] input[name=search]');
  field.focus();
  window.__before = field;
  window.__rowsBefore = document.querySelectorAll('[data-live-list] tbody tr').length;
  field.value = value;
  field.setSelectionRange(value.length, value.length);
  field.dispatchEvent(new Event('input', {bubbles: true}));
}
type(__QUERY__);

var tries = 0;
var timer = setInterval(function () {
  tries += 1;
  var rows = document.querySelectorAll('[data-live-list] tbody tr');
  var field = document.querySelector('form[data-live-filter] input[name=search]');
  var done = field.selectionStart === __LEN__ && rows.length !== window.__rowsBefore;
  if (!done && tries < 300) return;
  clearInterval(timer);
  document.title = 'RESULT' + JSON.stringify({
    stillFocused: document.activeElement === field,
    sameNode: window.__before === field,
    value: field.value,
    caret: field.selectionStart,
    caretEnd: field.selectionEnd,
    rows: rows.length,
    names: Array.prototype.map.call(
      document.querySelectorAll('[data-live-list] tbody tr td:nth-child(2)'),
      function (td) { return td.textContent.trim(); }),
    total: (document.querySelector('[data-live-list] .mt-4') || {}).textContent,
    replaced: window.__replaced,
  }) + 'END';
}, 10);
"""

INNER = """<!doctype html><html><head><meta charset="utf-8">
<style>__CSS__</style>
<script>
window.fetch = function (url) {
  return Promise.resolve({text: function () {
    return Promise.resolve(__ANSWER__);
  }});
};
/* `replaceState` под `file://` адрес не меняет, поэтому проверяем сам вызов, а не
   `location.search`: иначе проверка была бы вечно пустой. */
window.__replaced = [];
var realReplace = window.history.replaceState.bind(window.history);
window.history.replaceState = function (a, b, url) {
  window.__replaced.push(String(url));
  try { realReplace(a, b, url); } catch (e) { /* file:// — ожидаемо */ }
};
</script></head>
<body class="bg-surface text-primary" data-sidebar="expanded" data-nav="closed">
__BODY__
<script>__MEASURE__</script>
</body></html>"""

OUTER = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0}iframe{border:0;display:block;height:1200px}
</style></head><body>
<iframe id="f" width="1440" src="__INNER__"></iframe>
<pre id="out"></pre>
<script>
var timer = setInterval(function () {
  var f = document.getElementById('f');
  var d = f && f.contentDocument;
  if (!d || d.title.indexOf('RESULT') !== 0) { return; }
  clearInterval(timer);
  document.getElementById('out').textContent = d.title;
}, 5);
</script>
</body></html>"""

QUERY = "Гво"

SECTIONS = ("products", "orders")
"""Одна и та же пауза и одно и то же поле на обеих страницах: скрипт общий. На
заказах «Гво» — это имя клиента: после снятия списка «Клиент» страница ищет
именно по нему, и набор сужает список заказов."""


def _chrome() -> str | None:
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _render(section: str) -> tuple[str, str]:
    """Страница раздела и настоящий ответ сервера на отфильтрованный запрос."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_db
    from app.main import create_app
    from app.modules.auth.models import User
    from app.modules.auth.service import (
        ensure_default_roles, get_role_by_name, hash_password, login,
    )
    from app.modules.inventory.models import Product, Unit
    from app.modules.inventory.service import ensure_default_units
    from app.modules.settings.service import ensure_default_settings

    import app.modules.audit.models  # noqa: F401
    import app.modules.backup.models  # noqa: F401
    import app.modules.customers.models  # noqa: F401
    import app.modules.importer.models  # noqa: F401
    import app.modules.notifications.models  # noqa: F401
    import app.modules.orders.models  # noqa: F401
    import app.modules.settings.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as s:
        ensure_default_roles(s)
        ensure_default_settings(s)
        ensure_default_units(s)

    app = create_app()
    app.state.skip_seed_admin = True

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client, Session() as s:
        s.add(User(username="boss", password_hash=hash_password("password123"),
                   role_id=get_role_by_name(s, "admin").id))
        unit_id = s.query(Unit).first().id
        for name in ("Гвоздь", "Гвоздь усиленный", "Шуруп", "Доска"):
            s.add(Product(name=name, sku="s" + str(abs(hash(name)) % 1000),
                          price=10.0, rest=5, unit_id=unit_id))
        s.commit()
        client.cookies.set("session", login(s, "boss", "password123").token)
        if section == "orders":
            _orders(s, unit_id)
            page = client.get("/orders").text
            answer = client.get("/orders", params={"search": QUERY}).text
            return page, answer
        page = client.get("/products").text
        answer = client.get("/products", params={"search": QUERY}).text
    return page, answer


def _orders(session, unit_id) -> None:
    """Четыре заказа на четырёх клиентов: два названия содержат «Гво», поэтому
    набор сужает список, а вторую колонку («Клиент») видно в измерении."""
    from app.modules.customers.models import Customer
    from app.modules.inventory.models import Product
    from app.modules.orders.service import create_order

    product = Product(name="Гвоздь", sku="o-1", price=10.0, rest=50, unit_id=unit_id)
    session.add(product)
    session.commit()
    for index, name in enumerate(("Гвоздь", "Гвоздь усиленный", "Шуруп", "Доска")):
        customer = Customer(name=name)
        session.add(customer)
        session.commit()
        create_order(session, customer_id=customer.id, discount=0,
                     items=[{"product_id": product.id, "quantity": 1, "discount": 0}])


def _js_string(raw: str) -> str:
    """Ответ сервера в строку для скрипта.

    `</` обязателен: в странице есть `</script>`, и без экранирования он закрыл бы
    тег прямо в документе. Экранировать надо **после** `json.dumps`, а не до:
    `json.dumps` удваивает обратную косую черту, и `<\/` превратился бы в `\\\/`,
    то есть в значение ушёл бы обратный слэш, `DOMParser` не разобрал бы страницу
    и нашёл бы пустоту. Проверено: `fresh=false`, `rows=0`.
    """
    return json.dumps(raw, ensure_ascii=False).replace("</", "<\\/")


def _measure(section: str) -> dict:
    page, answer = _render(section)
    body = page[page.index("<body"): page.index("</body>")]
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    work = Path(tempfile.mkdtemp(prefix="live-filter-focus-"))
    inner = work / "inner.html"
    # `__MEASURE__` подставляется ДО `__QUERY__` и `__LEN__`: они лежат внутри
    # самой MEASURE, и обратный порядок оставил бы их в коде — скрипт не
    # разобрался бы молча, титл не появился бы, а тест упал бы с пустым выводом.
    inner.write_text(
        INNER.replace("__CSS__", css).replace("__ANSWER__", _js_string(answer))
             .replace("__BODY__", body).replace("__MEASURE__", MEASURE)
             .replace("__QUERY__", json.dumps(QUERY))
             .replace("__LEN__", str(len(QUERY))),
        encoding="utf-8")
    outer = work / "outer.html"
    outer.write_text(OUTER.replace("__INNER__", inner.resolve().as_uri()), encoding="utf-8")
    shell = (f'"{_chrome()}" --headless=new --disable-gpu --no-sandbox '
             f'--allow-file-access-from-files --window-size=1600,1400 '
             f'--virtual-time-budget=8000 --dump-dom "{outer.resolve().as_uri()}"')
    out = subprocess.run(shell, capture_output=True, text=True, encoding="utf-8")
    match = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.DOTALL)
    assert match, out.stdout[-3000:] + out.stderr[-2000:]
    raw = html_mod.unescape(match.group(1))
    assert raw.startswith("RESULT"), repr(raw[:400])
    return json.loads(raw[len("RESULT"):-len("END")])


@pytest.fixture(scope="module", params=SECTIONS)
def filtered(request) -> dict:
    if _chrome() is None:
        pytest.skip("Chrome не установлен")
    return _measure(request.param)


def test_the_search_box_keeps_the_focus_after_the_list_is_redrawn(filtered):
    """Главный дефект: после первой буквы фокус уходил на `<body>`."""
    assert filtered["sameNode"], "поле пересоздали — страница уехала целиком"
    assert filtered["stillFocused"], (
        "после перерисовки фокус не в поле, печатать дальше нечем")


def test_the_caret_stays_at_the_end_of_what_was_typed(filtered):
    """Возврат каретки в начало тихо переворачивал бы набор: человек допечатывал
    слово, а оно оказывалось задом наперёд."""
    assert filtered["value"] == QUERY, f"в поле {filtered['value']!r}"
    assert filtered["caret"] == len(QUERY), (
        f"каретка на {filtered['caret']}, а набирали {len(QUERY)} символов")
    assert filtered["caretEnd"] == len(QUERY), "выделен кусок слова, а не его конец"


def test_the_list_really_was_filtered(filtered):
    """Без этого остальные проверки ничего не значат: можно удержать фокус и не
    перерисовать список вовсе."""
    assert filtered["names"], "список пуст, фильтрация не произошла"
    for name in filtered["names"]:
        assert "Гво" in name, f"в отфильтрованном списке осталось {name!r}"
    assert filtered["replaced"], "адресная строка не обновилась, перезагрузка потеряет фильтр"
    assert any("search=" in url for url in filtered["replaced"]), (
        f"в адрес не попал фильтр: {filtered['replaced']}")


def test_the_total_is_redrawn_together_with_the_list(filtered):
    """«Всего» и постраничные ссылки лежат рядом со списком, но не внутри него.
    Если бы они остались снаружи, список отфильтровался бы, а счётчик и
    «Вперёд» показывали бы прежние числа."""
    assert "Всего:" in (filtered["total"] or ""), (
        f"подвал с итогом не перерисован: {filtered['total']!r}")


def test_the_field_the_script_touches_is_only_the_search_box():
    """Скрипт не имеет права пересоздавать поле или отправлять форму: и то и
    другое уводит фокус. Проверка на исходнике — она ловит само возвращение к
    полной отправке даже там, где Chrome нет."""
    base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    block = base[base.index("liveFilterDebounce") - 400:]
    assert "requestSubmit" not in block, (
        "живая фильтрация снова отправляет форму: страница перерисовывается "
        "целиком и фокус уходит")
    assert "list.innerHTML" in block, "список не перерисовывается на месте"