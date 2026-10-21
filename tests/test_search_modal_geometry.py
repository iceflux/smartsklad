"""Поиск из шапки открывается ОКНОМ, и шапка от этого не меняется.

Дефект, ради которого всё затевалось: лупа раскрывала поле прямо в шапке, шапка —
flex-строка, и растущее поле выталкивало кнопки и меняло её высоту. Владелец это
видел. Проверка строк разметки такой дефект не увидит вообще, поэтому здесь
настоящий браузер и настоящая геометрия.

Что меряется одним прогоном:

* высота шапки до открытия и после — обязана совпасть до долей пикселя;
* горизонтальное положение кнопок в шапке — тоже: «сдвинуло кнопки» было половиной
  жалобы, и молчаливое движение вправо заметило бы того, кто смотрит;
* поле осталось в фокусе, а каретка — в конце набранного: без этого дописать
  слово нельзя, и это был бы второй дефект того же места;
* окно действительно на экране (`elementFromPoint` по центру панели), иначе все
  проверки высоты прошли бы на закрытом окне;
* ошибки страничного скрипта попадают в ответ. Харнесс здесь один раз закончился
  кодом 0 при пустом выводе, и проверка прошла бы, ничего не сделав; поэтому
  ошибки собираются и asserted, а не молча проглатываются.

Сеть подменена: из `file://` настоящий `fetch` не ходит, а разбирать HTML должен
настоящий `DOMParser`.
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

QUERY = "Гво"

MEASURE = r"""
window.__errors = [];
window.addEventListener('error', function (e) { window.__errors.push(String(e.message)); });
window.addEventListener('unhandledrejection', function (e) {
  window.__errors.push('rejection: ' + String((e && e.reason) || e));
});

function box(node) { var r = node.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }
function header() { return document.querySelector('header'); }

var before = {header: box(header()), add: box(document.querySelector('a[href="/orders/new"]')),
              magnifier: box(document.querySelector('[data-search-open]'))};

document.querySelector('[data-search-open]').click();

var field = document.querySelector('[data-search-field]');
field.focus();
field.value = __QUERY__;
field.setSelectionRange(__QUERY__.length, __QUERY__.length);
field.dispatchEvent(new Event('input', {bubbles: true}));

var tries = 0;
var timer = setInterval(function () {
  tries += 1;
  var results = document.querySelector('[data-search-results]');
  var done = results && results.innerHTML.indexOf('Гвоздь') !== -1;
  if (!done && tries < 400) return;
  clearInterval(timer);
  var panel = document.querySelector('.app-modal-panel');
  var rect = panel.getBoundingClientRect();
  document.title = 'RESULT' + JSON.stringify({
    query: __QUERY__,
    before: before,
    after: {header: box(header()), add: box(document.querySelector('a[href="/orders/new"]')),
            magnifier: box(document.querySelector('[data-search-open]'))},
    open: document.querySelector('[data-modal="search"]').getAttribute('data-open'),
    expanded: document.querySelector('[data-search-open]').getAttribute('aria-expanded'),
    panelVisible: document.elementFromPoint(rect.left + rect.width / 2, rect.top + 12) !== null
                  && panel.getBoundingClientRect().height > 0,
    stillFocused: document.activeElement === field,
    value: field.value,
    caret: field.selectionStart,
    caretEnd: field.selectionEnd,
    hits: results ? results.querySelectorAll('a').length : 0,
    names: results ? Array.prototype.map.call(results.querySelectorAll('a'),
            function (a) { return a.textContent; }) : [],
    errors: window.__errors,
  }) + 'END';
}, 10);
"""

INNER = """<!doctype html><html><head><meta charset="utf-8">
<style>__CSS__</style>
<script>
window.fetch = function (url) {
  return Promise.resolve({text: function () { return Promise.resolve(__ANSWER__); }});
};
</script></head>
<body class="bg-surface text-primary" data-sidebar="expanded" data-nav="closed">
__BODY__
<script>__MEASURE__</script>
</body></html>"""

OUTER = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0}iframe{border:0;display:block;height:900px}
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


def _chrome() -> str | None:
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _js_string(raw: str) -> str:
    return json.dumps(raw, ensure_ascii=False).replace("</", "<\\/")


def _measure(client) -> dict:
    page = client.get("/orders").text
    body = page[page.index("<body"): page.index("</body>")]
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    answer = client.get("/api/search/table", params={"q": QUERY}).text
    work = Path(tempfile.mkdtemp(prefix="search-modal-"))
    inner = work / "inner.html"
    inner.write_text(
        INNER.replace("__CSS__", css).replace("__ANSWER__", _js_string(answer))
             .replace("__BODY__", body).replace("__MEASURE__", MEASURE)
             .replace("__QUERY__", json.dumps(QUERY)),
        encoding="utf-8")
    outer = work / "outer.html"
    outer.write_text(OUTER.replace("__INNER__", inner.resolve().as_uri()), encoding="utf-8")
    shell = (f'"{_chrome()}" --headless=new --disable-gpu --no-sandbox '
             f'--allow-file-access-from-files --window-size=1600,1000 '
             f'--virtual-time-budget=10000 --dump-dom "{outer.resolve().as_uri()}"')
    out = subprocess.run(shell, shell=True, capture_output=True, text=True, encoding="utf-8")
    match = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.DOTALL)
    assert match, out.stdout[-3000:] + out.stderr[-2000:]
    raw = html_mod.unescape(match.group(1))
    assert raw.startswith("RESULT"), (
        "страница не отдала результат: либо скрипт упал, либо прибор ничего не дождался. "
        f"Получено: {raw[:400]!r}"
    )
    return json.loads(raw[len("RESULT"):-len("END")])


@pytest.fixture(scope="module")
def measured():
    """Отдельный клиент: нужен настоящий вход и база, которой не касается общая."""
    if _chrome() is None:
        pytest.skip("Chrome не установлен")
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_db
    from app.main import create_app
    from app.modules.auth.models import User
    from app.modules.auth.service import ensure_default_roles, get_role_by_name, hash_password, login
    from app.modules.customers.models import Customer
    from app.modules.inventory.models import Product, Unit
    from app.modules.inventory.service import ensure_default_units
    from app.modules.settings.service import ensure_default_settings

    import app.modules.audit.models  # noqa: F401
    import app.modules.backup.models  # noqa: F401
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
        s.add(Product(name="Гвоздь", sku="g-1", price=10.0, rest=5, unit_id=unit_id))
        s.add(Product(name="Гвоздь усиленный", sku="g-2", price=10.0, rest=5, unit_id=unit_id))
        s.add(Customer(name="Гвоздица"))
        s.commit()
        client.cookies.set("session", login(s, "boss", "password123").token)
        return _measure(client)


def test_the_header_keeps_its_height_when_the_search_opens(measured):
    """Главный дефект: высота шапки менялась, и это видел владелец."""
    assert measured["open"] == "true", "окно не открылось, высоту судить не о чем"
    assert measured["after"]["header"][3] == pytest.approx(
        measured["before"]["header"][3], abs=0.01), (
        f"шапка выросла на "
        f"{measured['after']['header'][3] - measured['before']['header'][3]:.2f} px: "
        "поле вернулось в шапку")


def test_the_header_buttons_do_not_move(measured):
    """Вторая половина жалобы: растущее поле сдвигало кнопки вправо."""
    for what in ("add", "magnifier"):
        for index, edge in ((0, "левый край"), (2, "ширина")):
            assert measured["after"][what][index] == pytest.approx(
                measured["before"][what][index], abs=0.01), (
                f"{what}: {edge} сдвинулся на "
                f"{measured['after'][what][index] - measured['before'][what][index]:.2f} px")


def test_the_window_is_really_on_screen(measured):
    assert measured["panelVisible"], "панель не отрисована, а проверки высоты молчат"
    assert measured["expanded"] == "true", "кнопка не объявила, что окно открыто"


def test_the_results_arrive_and_the_field_keeps_the_caret(measured):
    assert measured["names"], "результаты не приехали, проверки фокуса ничего не значат"
    assert all("Гво" in name for name in measured["names"]), measured["names"]
    assert measured["stillFocused"], (
        "фокус ушёл из поля после результатов: дописать слово нечем")
    assert measured["value"] == QUERY, f"в поле {measured['value']!r}"
    assert measured["caret"] == len(QUERY), (
        f"каретка на {measured['caret']}, а набирали {len(QUERY)} символов")
    assert measured["caretEnd"] == len(QUERY), "выделен кусок слова, а не его конец"


def test_no_page_script_failed(measured):
    """Молчаливый прогон — тот класс дефекта, который здесь уже проходил как успех."""
    assert measured["errors"] == [], (
        "страничный скрипт сообщил об ошибке: " + repr(measured["errors"]))