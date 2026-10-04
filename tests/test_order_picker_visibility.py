"""Список подстановки должен быть виден на самом деле, а не только в разметке.

Дефект, который этот тест ловит, не ловится проверкой «в разметке есть `<ul>`»:
список **был** в разметке, **был** `position: absolute` и **не был виден** —
его обрезал предок `div.overflow-x-auto` вокруг таблицы позиций. `overflow-x: auto`
по спецификации тянет за собой `overflow-y: auto`, то есть обёртка становится
контейнером прокрутки по обеим осям и обрезает даже абсолютно позиционированных
потомков. Замером: при высоте списка 110px показывалось 6.

Поэтому проверка здесь одна и она поведенческая: `document.elementFromPoint` в
центре списка обязан вернуть сам список или его потомка. На обрезанном списке он
вернёт то, что под ним, — на элемент, который виден. Проверка «список есть в DOM»
прошла бы на той же поломанной странице.

Тем же прибором проверяется окно выбора товара и форма создания товара: там
предмет тот же самый. Кнопка «Выбрать товар» может лежать в разметке и при этом
не наезжать ни на что, а окно — открыться и оказаться за краем экрана; и то и
другое «есть в DOM» не отличает от рабочего.

Тест гоняет настоящий рендер `/orders/new` через TestClient и собранный
`app/static/app.css` в headless Chrome — выдуманный DOM ничего не знает о
раскладке, а раскладка здесь и есть предмет проверки.

Пропускается, если Chrome не установлен. Node не нужен: этот тест не fake-DOM
харнесс, и без node он всё равно выполняется.
"""

import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
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
function own(el, node) {
  for (var n = node; n; n = n.parentElement) { if (n === el) return true; }
  return false;
}
function rect(el) {
  var r = el.getBoundingClientRect();
  return {x: Math.round(r.x), y: Math.round(r.y),
          w: Math.round(r.width), h: Math.round(r.height),
          bottom: Math.round(r.bottom), right: Math.round(r.right)};
}
var boxes = document.querySelectorAll('.app-picker');
for (var b = 0; b < boxes.length; b++) {
  var text = boxes[b].querySelector('input[type=text]');
  text.value = 'Гво';
  text.dispatchEvent(new Event('input', {bubbles: true}));
}

function measure() {
  var out = [];
  for (var b = 0; b < boxes.length; b++) {
    var box = boxes[b];
    var list = box.querySelector('.app-picker-list');
    var r = rect(list);
    var field = box.querySelector('input[type=text]').getBoundingClientRect();
    var hit = (document.elementFromPoint(Math.round(r.x + r.w / 2),
                                          Math.round(r.y + r.h / 2)) || {});
    out.push({
      where: b === 0 ? 'customer' : 'line',
      open: box.getAttribute('data-open'),
      list: r,
      hitsItself: own(list, hit),
      hitClass: hit.className || hit.tagName || '(нет)',
      insideViewport: r.y >= 0 && r.bottom <= document.documentElement.clientHeight,
      gap: Math.round(r.y - field.bottom),
      fieldLeft: Math.round(field.left),
    });
  }
  return out;
}

function fill() {
  for (var b = 0; b < boxes.length; b++) {
    if (!boxes[b].querySelector('.app-picker-list').children.length) return false;
  }
  return boxes.length > 0;
}

var tries = 0;
var timer = setInterval(function () {
  tries += 1;
  if (!fill() && tries < 300) return;
  var first = measure();
  /* Распорка снизу нужна, чтобы документ стал прокручиваемым: без неё окно
     нельзя сдвинуть и проверять, что список остался при поле. Это подпорка
     прибора, а не правка приложения. */
  var spacer = document.createElement('div');
  spacer.style.height = '900px';
  document.body.appendChild(spacer);
  window.scrollTo(0, 150);
  /* Событие прокрутки шлём сами: под `--virtual-time-budget` Chrome не доставляет
     своё собственное событие прокрутки (проверено — без этой строки зазор остаётся
     154px, с ней 4px, при реально сдвинутом окне). Проверяется поведение скрипта
     «на прокрутке список возвращается под поле», а не доставка события браузером. */
  document.dispatchEvent(new Event('scroll'));
  setTimeout(function () {
    var after = measure();
    document.title = 'RESULT' + JSON.stringify({
      first: first, afterScroll: after,
      scrolled: window.scrollY,
      viewport: {w: document.documentElement.clientWidth,
                 h: document.documentElement.clientHeight},
    }) + 'END';
  }, 60);
}, 10);
"""

INNER = """<!doctype html><html><head><meta charset="utf-8">
<style>__CSS__</style>
<script>
/* Подмена fetch ДО скрипта подстановки: предмет проверки — раскладка, а не сеть,
   а data:-URL ломается о хвост ?q=. Настоящие `paint()` и `place()` при этом
   отрабатывают по-настоящему, то есть замер идёт по рабочему коду. */
window.fetch = function () {
  return Promise.resolve({json: function () {
    return Promise.resolve(__ANSWER__);
  }});
};
</script></head>
<body class="bg-surface text-primary" data-sidebar="expanded" data-nav="closed">
__BODY__
<script>__MEASURE__</script>
</body></html>"""

OUTER = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0}iframe{border:0;display:block;height:1200px}
</style></head><body>
<iframe id="f" width="__WIDTH__" src="__INNER__"></iframe>
<pre id="out"></pre>
<script>
var timer = setInterval(function () {
  var f = document.getElementById('f');
  var d = f && f.contentDocument;
  if (!d) { return; }
  /* Ошибка скрипта страницы видна иначе только пустым выводом: прибор молчал бы,
     и падение выглядело бы как «Chrome ничего не измерил». */
  if (d.title.indexOf('ERROR') === 0) {
    clearInterval(timer);
    document.getElementById('out').textContent = d.title;
    return;
  }
  if (d.title.indexOf('RESULT') !== 0) { return; }
  clearInterval(timer);
  document.getElementById('out').textContent = d.title;
}, 5);
</script>
</body></html>"""

WIDTH = 1440

ANSWER_JSON = json.dumps({
    "items": [
        {"id": 1, "label": "Гвоздь 1", "hint": "10.00 руб"},
        {"id": 2, "label": "Гвоздь 2", "hint": "10.00 руб"},
        {"id": 3, "label": "Гвоздь 3", "hint": "10.00 руб"},
    ],
    "total": 3, "limit": 10,
}, ensure_ascii=False)


def _chrome() -> str | None:
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _render(permissions: list[str] | None = None) -> str:
    """Настоящая страница `/orders/new` с клиентом и товарами. `permissions` —
    роль настраиваемая: проверке недоступной кнопки нужен человек без права на
    товары, а у администратора оно есть всегда."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_db
    from app.main import create_app
    from app.modules.auth.models import Role, User
    from app.modules.auth.service import (
        ensure_default_roles, get_role_by_name, hash_password, login,
    )
    from app.modules.customers.models import Customer
    from app.modules.inventory.models import Product, Unit
    from app.modules.inventory.service import ensure_default_units
    from app.modules.settings.service import ensure_default_settings

    import app.modules.audit.models  # noqa: F401
    import app.modules.backup.models  # noqa: F401
    import app.modules.importer.models  # noqa: F401
    import app.modules.notifications.models  # noqa: F401
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
        if permissions is None:
            role_id = get_role_by_name(s, "admin").id
        else:
            role = Role(name="clerk", is_system=False, is_super=False,
                        permissions=list(permissions))
            s.add(role)
            s.flush()
            role_id = role.id
        s.add(User(username="boss", password_hash=hash_password("password123"),
                   role_id=role_id))
        s.add(Customer(name="Ромашка", personal_discount=0))
        unit_id = s.query(Unit).first().id
        for n in range(1, 4):
            s.add(Product(name=f"Гвоздь {n}", sku=str(n), price=10.0, rest=5,
                          unit_id=unit_id))
        s.commit()
        client.cookies.set("session", login(s, "boss", "password123").token)
        return client.get("/orders/new").text


def _measure() -> dict:
    page = _render()
    body = page[page.index("<body"): page.index("</body>")]
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    work = Path(tempfile.mkdtemp(prefix="picker-visibility-"))
    inner = work / "inner.html"
    inner.write_text(
        INNER.replace("__CSS__", css).replace("__ANSWER__", ANSWER_JSON)
             .replace("__BODY__", body).replace("__MEASURE__", MEASURE),
        encoding="utf-8")
    outer = work / "outer.html"
    outer.write_text(
        OUTER.replace("__WIDTH__", str(WIDTH)).replace("__INNER__", inner.resolve().as_uri()),
        encoding="utf-8")
    # `--allow-file-access-from-files` обязателен: каждый file:// в Chrome — свой
    # непрозратый origin, и без флага contentDocument из родителя недостижим.
    shell = (f'"{_chrome()}" --headless=new --disable-gpu --no-sandbox '
             f'--allow-file-access-from-files --window-size=1600,1400 '
             f'--virtual-time-budget=8000 --dump-dom "{outer.resolve().as_uri()}"')
    out = subprocess.run(shell, capture_output=True, text=True, encoding="utf-8")
    match = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.DOTALL)
    assert match, out.stdout[-3000:] + out.stderr[-2000:]
    raw = html_mod.unescape(match.group(1))
    assert raw.startswith("RESULT"), repr(raw[:400])
    return json.loads(raw[len("RESULT"):-len("END")])


@pytest.fixture(scope="module")
def measured() -> dict:
    if _chrome() is None:
        pytest.skip("Chrome не установлен")
    return _measure()


def _row(rows: list, where: str) -> dict:
    return next(r for r in rows if r["where"] == where)


def test_the_product_line_sits_outside_the_scrolling_table_wrapper(measured):
    """Список позиции заказа лежит внутри `div.overflow-x-auto`, то есть внутри
    контейнера прокрутки по обеим осям. `position: absolute` оттуда не спасает —
    спасает только выход из потока обрезания, то есть `fixed`."""
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    rule = re.search(r"\.app-picker-list\{([^}]*)\}", css)
    assert rule, "правило .app-picker-list не доехало до сборки"
    assert "position:fixed" in rule.group(1).replace(" ", ""), (
        "список подстановки остался в потоке обрезания: "
        f"{rule.group(1)[:120]}")


def test_the_list_of_a_product_line_is_really_on_the_screen(measured):
    """Главная проверка: в центре списка должен лежать сам список. На обрезанном
    списке там лежит то, что под ним, — при 110px высоты показывалось 6."""
    line = _row(measured["first"], "line")
    assert line["open"] == "true", f"список не открылся: {line}"
    assert line["list"]["h"] > 40, f"список пустой или схлопнулся: {line['list']}"
    assert line["hitsItself"], (
        f"в центре списка лежит {line['hitClass']!r}, а не он сам: {line['list']}")
    assert line["insideViewport"], f"список ушёл за пределы экрана: {line['list']}"


def test_the_customer_list_is_not_made_worse_by_the_fix(measured):
    """Клиент лежит вне таблицы и был виден всегда. Правка позиционирования
    не должна сломать и его — иначе «починил одно, сломал второе»."""
    customer = _row(measured["first"], "customer")
    assert customer["hitsItself"], (
        f"список клиента перестал быть виден: {customer['list']} → {customer['hitClass']!r}")
    assert customer["insideViewport"], f"список клиента ушёл за экран: {customer['list']}"


def test_the_list_stays_attached_to_its_field_when_the_page_scrolls(measured):
    """`position: fixed` без переноса координат оставляет список на месте, а поле
    уезжает: список остаётся висеть в пустоте. Поэтому скрипт переставляет его на
    прокрутке и на изменении размера окна."""
    assert measured["scrolled"] > 0, "страница не прокрутилась, проверка ни о чём"
    for where in ("customer", "line"):
        after = _row(measured["afterScroll"], where)
        # 4, а не 2: зазор считается как разность двух Math.round, то есть сам
        # плюс-минус пиксель округления на каждом конце.
        assert abs(after["gap"]) <= 4, (
            f"список {where} оторвался от поля после прокрутки: "
            f"поле ушло, список остался, зазор {after['gap']}px")


INNER_MODAL = """<!doctype html><html><head><meta charset="utf-8">
<style>__CSS__</style>
<script>
/* Сеть подменена, всё остальное — настоящее: раскладку выдуманный DOM не знает,
   а предмет здесь и есть раскладка. Создание товара отвечает отдельно от
   подстановки, иначе выбор из окна и создание нельзя было бы различить.
   Ошибка скрипта объявляется заголовком: иначе прибор молчал бы пустым выводом,
   и переименование селектора выглядело бы как «Chrome ничего не измерил». */
window.onerror = function (message, source, line) {
  document.title = 'ERROR ' + message + ' @' + line;
};
window.fetch = function (url, sent) {
  var post = sent && sent.method === 'POST';
  return Promise.resolve({ok: true, json: function () {
    return Promise.resolve(post ? __CREATED__ : __ANSWER__);
  }});
};
</script></head>
<body class="bg-surface text-primary" data-sidebar="expanded" data-nav="closed">
__BODY__
<script>__MEASURE__</script>
</body></html>"""

MODAL_MEASURE = r"""
function own(el, node) {
  for (var n = node; n; n = n.parentElement) { if (n === el) return true; }
  return false;
}
function rect(el) {
  var r = el.getBoundingClientRect();
  return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
          h: Math.round(r.height)};
}
function hits(el) {
  if (!el) return {hit: false};
  var r = el.getBoundingClientRect();
  var probe = document.elementFromPoint(Math.round(r.x + r.width / 2),
                                        Math.round(r.y + r.height / 2));
  return {hit: own(el, probe), onScreen: r.width > 0 && r.height > 0
          && r.y >= 0 && r.bottom <= document.documentElement.clientHeight,
          by: (probe && (probe.className || probe.tagName)) || '(нет)'};
}
function read(box) {
  return {
    hidden: box.querySelector('input[type=hidden]').value,
    shown: box.querySelector('input[type=text]').value,
  };
}

var boxes = document.querySelectorAll('.app-picker');
var line = boxes[boxes.length - 1];
var browseBtn = line.querySelector('[data-picker-browse]');
var createBtn = line.querySelector('[data-picker-create]');
var browse = document.querySelector('[data-modal=browse]');
var create = document.querySelector('[data-modal=create]');
var out = {button: hits(browseBtn), before: read(line)};

browseBtn.click();
setTimeout(function () {
  var panel = browse.querySelector('.app-modal-panel');
  var options = browse.querySelector('[data-modal-list]').querySelectorAll('button');
  out.window = {open: browse.getAttribute('data-open'), panel: hits(panel),
                options: options.length};
  out.firstOption = {hit: hits(options[0]),
                     text: options[0] ? options[0].textContent : ''};
  options[1].click();
  setTimeout(function () {
    out.chosen = read(line);
    out.afterChoose = browse.getAttribute('data-open');
    browseBtn.click();
    setTimeout(function () {
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      out.escaped = browse.getAttribute('data-open');
      out.afterEscape = read(line);
      createBtn.click();
      setTimeout(function () {
        var form = create.querySelector('[data-create-form]');
        form.querySelector('input[name=name]').value = 'Гвоздь 3мм';
        out.createWindow = {open: create.getAttribute('data-open'),
                            panel: hits(create.querySelector('.app-modal-panel'))};
        form.querySelector('button').click();
        setTimeout(function () {
          out.created = read(line);
          out.createClosed = create.getAttribute('data-open');
          document.title = 'RESULT' + JSON.stringify(out) + 'END';
        }, 200);
      }, 100);
    }, 100);
  }, 100);
}, 150);
"""

CREATED_JSON = json.dumps({"id": 77, "name": "Гвоздь 3мм"}, ensure_ascii=False)

ADD_ROW_MEASURE = r"""
/* Ошибка скрипта собирается в отчёт, а не только в заголовок: кнопка «Добавить
   строку» зовёт `addItemRow()`, объявленную внутри IIFE, и в глобальной области
   её нет — Chrome даёт `Uncaught ReferenceError` и кнопка молчит. Проверка
   «кнопка есть в разметке» на такой странице проходит, а строки не появляется. */
var errors = [];
window.onerror = function (message, source, line) { errors.push(message + ' @' + line); };
function rows() { return document.getElementById('items-body').querySelectorAll('tr').length; }
/* Кнопка ищется по новому признаку, а если его нет — по прежнему тексту среди
   кнопок с inline-обработчиком. Иначе прибор упал бы на `null.click()` и
   сообщил бы пустым выводом, то есть молча, а на самой странице до правки
   именно так и было. */
var add = document.querySelector('[data-item-add]');
if (!add) {
  var inline = document.querySelectorAll('button[onclick]');
  for (var i = 0; i < inline.length; i += 1) {
    if (inline[i].textContent.indexOf('Добавить строку') !== -1) { add = inline[i]; }
  }
}
var before = rows();
var found = !!add;
if (add) {
  add.click();
  add.click();
}
setTimeout(function () {
  var body = document.getElementById('items-body');
  document.title = 'RESULT' + JSON.stringify({
    found: found, before: before, after: rows(), errors: errors,
    defined: typeof window.addItemRow,
    inlineHandler: add ? add.getAttribute('onclick') : null,
    cells: body.querySelectorAll('tr input[type=hidden]').length,
    pickers: body.querySelectorAll('.app-picker').length,
  }) + 'END';
}, 120);
"""


def _dump(inner_html: str, work: Path, width: int = 1440) -> str:
    inner = work / "inner.html"
    inner.write_text(inner_html, encoding="utf-8")
    outer = work / "outer.html"
    outer.write_text(OUTER.replace("__WIDTH__", str(width))
                     .replace("__INNER__", inner.resolve().as_uri()), encoding="utf-8")
    shell = (f'"{_chrome()}" --headless=new --disable-gpu --no-sandbox '
             f'--allow-file-access-from-files --window-size={width + 160},1400 '
             f'--virtual-time-budget=8000 --dump-dom "{outer.resolve().as_uri()}"')
    out = subprocess.run(shell, capture_output=True, text=True, encoding="utf-8")
    match = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.DOTALL)
    assert match, out.stdout[-3000:] + out.stderr[-2000:]
    raw = html_mod.unescape(match.group(1))
    assert raw.startswith("RESULT"), repr(raw[:400])
    return raw[len("RESULT"):-len("END")]


def _measure_add_row() -> dict:
    page = _render()
    body = page[page.index("<body"): page.index("</body>")]
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    work = Path(tempfile.mkdtemp(prefix="picker-addrow-"))
    inner = (INNER.replace("__CSS__", css).replace("__BODY__", body)
             .replace("__MEASURE__", ADD_ROW_MEASURE))
    return json.loads(_dump(inner, work))


def _measure_modal() -> dict:
    page = _render()
    body = page[page.index("<body"): page.index("</body>")]
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    work = Path(tempfile.mkdtemp(prefix="picker-modal-"))
    inner = work / "inner.html"
    inner.write_text(
        INNER_MODAL.replace("__CSS__", css).replace("__ANSWER__", ANSWER_JSON)
                   .replace("__CREATED__", CREATED_JSON)
                   .replace("__BODY__", body)
                   .replace("__MEASURE__", MODAL_MEASURE),
        encoding="utf-8")
    outer = work / "outer.html"
    outer.write_text(
        OUTER.replace("__WIDTH__", str(WIDTH)).replace("__INNER__", inner.resolve().as_uri()),
        encoding="utf-8")
    shell = (f'"{_chrome()}" --headless=new --disable-gpu --no-sandbox '
             f'--allow-file-access-from-files --window-size=1600,1400 '
             f'--virtual-time-budget=8000 --dump-dom "{outer.resolve().as_uri()}"')
    out = subprocess.run(shell, capture_output=True, text=True, encoding="utf-8")
    match = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.DOTALL)
    assert match, out.stdout[-3000:] + out.stderr[-2000:]
    raw = html_mod.unescape(match.group(1))
    assert raw.startswith("RESULT"), repr(raw[:400])
    return json.loads(raw[len("RESULT"):-len("END")])


@pytest.fixture(scope="module")
def modal() -> dict:
    if _chrome() is None:
        pytest.skip("Chrome не установлен")
    return _measure_modal()


def test_the_picker_buttons_are_really_pressable(modal):
    """Кнопка, которая есть в разметке, но не на экране, — это ровно тот дефект,
    о котором предупреждает бриф: проверка «кнопка есть» прошла бы на странице,
    где по ней нельзя нажать."""
    assert modal["button"]["onScreen"], modal["button"]
    assert modal["button"]["hit"], f"в центре кнопки лежит {modal['button']['by']}"


def test_the_browse_window_is_really_on_the_screen(modal):
    assert modal["window"]["open"] == "true", modal
    assert modal["window"]["panel"]["onScreen"], modal["window"]["panel"]
    assert modal["window"]["panel"]["hit"], (
        f"в центре окна лежит {modal['window']['panel']['by']}")
    assert modal["window"]["options"] == 3, modal["window"]
    assert modal["firstOption"]["hit"]["onScreen"] and modal["firstOption"]["hit"]["hit"], (
        f"пункт списка не на экране: {modal['firstOption']}")


def test_the_chosen_product_fills_the_order_line(modal):
    assert modal["chosen"]["hidden"] == "2", modal["chosen"]
    assert modal["chosen"]["shown"] == "Гвоздь 2", modal["chosen"]
    assert modal["afterChoose"] == "false", "окно осталось открытым поверх заполненной строки"


def test_escape_closes_the_window_without_choosing_anything(modal):
    assert modal["escaped"] == "false", "Escape не закрыл окно"
    assert modal["afterEscape"] == modal["chosen"], (
        f"Escape подменил товар: {modal['afterEscape']}")


def test_the_create_window_is_on_the_screen_and_its_product_fills_the_line(modal):
    assert modal["createWindow"]["open"] == "true", modal
    assert modal["createWindow"]["panel"]["onScreen"] and modal["createWindow"]["panel"]["hit"], \
        modal["createWindow"]["panel"]
    assert modal["created"] == {"hidden": "77", "shown": "Гвоздь 3мм"}, (
        f"созданный товар не стал выбранным в строке: {modal['created']}")
    assert modal["createClosed"] == "false", "форма создания осталась открытой"


def test_the_window_rules_reach_the_built_stylesheet():
    """Окно открыто атрибутом `data-open`, и правило живёт в артефакте: класс,
    выпавший из сборки, дал бы окно в разметке и полноэкранную пустоту на экране."""
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    hidden = re.search(r"\.app-modal\{([^}]*)\}", css)
    assert hidden, "правило .app-modal не доехало до сборки"
    assert "display:none" in hidden.group(1).replace(" ", ""), hidden.group(1)
    opened = re.search(r"\.app-modal\[data-open[^\]]*\]\{([^}]*)\}", css)
    assert opened, "окно не открывается: правила по data-open нет в сборке"
    assert "display:flex" in opened.group(1).replace(" ", ""), opened.group(1)
    panel = re.search(r"\.app-modal-panel\{([^}]*)\}", css)
    assert panel and panel.group(1).strip(), "панель окна без правил"
    # Артефакт собран и лежит в репозитории, поэтому проверка только его не
    # поймала бы вычищенное правило в исходнике: сборку никто не запускал бы.
    source = (ROOT / "app" / "static" / "tailwind.css").read_text(encoding="utf-8")
    block = re.search(r"\.app-modal\[data-open=\"true\"\]\s*\{([^}]*)\}", source)
    assert block, "в исходнике нет правила, открывающего окно"
    assert "display: flex" in block.group(1), (
        f"окно не открывается: {block.group(1)!r}")


def test_the_list_is_placed_by_the_script_under_its_field():
    """Без координат, которые ставит скрипт, список `position: fixed` повис бы в
    углу вьюпорта: `fixed` без `top`/`left` — это угол. Проверка на исходнике
    скрипта, а не на поведении браузера, — она ловит само удаление координат
    даже тогда, когда Chrome на машине нет."""
    form = (ROOT / "app" / "templates" / "order_form.html").read_text(encoding="utf-8")
    script = form[form.index("<script>"):]
    assert "getBoundingClientRect" in script, "скрипт не меряет поле"
    for prop in ("top", "left", "width"):
        assert re.search(r"list\.style\." + prop, script), (
            f"скрипт не ставит list.style.{prop}: список оторвётся от поля")
    show = re.search(r"function show\([^)]*\)\s*\{(.*?)\n    \}", script, re.DOTALL)
    assert show, "не нашёл функцию show"
    assert "place(state)" in show.group(1), (
        "открытие списка не переставляет его под поле: place() не вызывается из show()")


def test_the_list_is_repositioned_on_scroll_and_on_resize():
    """Список `fixed` без этого остаётся на экране, а поле уезжает."""
    form = (ROOT / "app" / "templates" / "order_form.html").read_text(encoding="utf-8")
    script = form[form.index("<script>"):]
    assert re.search(r'addEventListener\("scroll",\s*reposition,\s*true\)', script), \
        "нет переноса на прокрутке"
    assert 'addEventListener("resize", reposition)' in script, \
        "нет переноса на изменении размера окна"


@pytest.fixture(scope="module")
def added() -> dict:
    if _chrome() is None:
        pytest.skip("Chrome не установлен")
    return _measure_add_row()


ORDER_RIGHTS = ["orders.view", "orders.create", "products.view"]

BLOCKED_MEASURE = r"""
/* Кнопка, которой право недоступно, обязана оставаться нажимаемой и видимой:
   настоящий `disabled` не получает событий click и не берётся с клавиатуры, то
   есть ни объяснения показать, ни с клавиатуры дойти. Прибор меряет и то, и
   другое, и то, что плашка действительно появилась на экране. */
var errors = [];
window.onerror = function (message, source, line) { errors.push(message + ' @' + line); };
function box(el) {
  var r = el.getBoundingClientRect();
  return {w: Math.round(r.width), h: Math.round(r.height), y: Math.round(r.y)};
}
var button = document.querySelector('[data-picker-create]');
var notice = document.querySelector('[data-picker-notice]');
var modal = document.querySelector('[data-modal=create]');
var out = {
  disabledAttribute: button.getAttribute('disabled'),
  ariaDisabled: button.getAttribute('aria-disabled'),
  noticeBefore: notice.textContent.trim(),
  noticeBeforeBox: box(notice),
};
button.focus();
out.focused = document.activeElement === button;
out.clicked = button.click();
setTimeout(function () {
  out.noticeAfter = notice.textContent.trim();
  out.noticeAfterBox = box(notice);
  var closer = notice.querySelector('[data-notice-close]');
  out.closer = closer ? closer.textContent : null;
  out.modalOpen = modal.getAttribute('data-open');
  out.errors = errors;
  document.title = 'RESULT' + JSON.stringify(out) + 'END';
}, 100);
"""


def _measure_blocked() -> dict:
    page = _render(ORDER_RIGHTS)
    body = page[page.index("<body"): page.index("</body>")]
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    work = Path(tempfile.mkdtemp(prefix="picker-blocked-"))
    inner = (INNER.replace("__CSS__", css).replace("__BODY__", body)
             .replace("__MEASURE__", BLOCKED_MEASURE))
    return json.loads(_dump(inner, work))


@pytest.fixture(scope="module")
def blocked() -> dict:
    if _chrome() is None:
        pytest.skip("Chrome не установлен")
    return _measure_blocked()


def test_the_unavailable_button_is_focusable(blocked):
    """Главное поведение `aria-disabled`: кнопка берётся с клавиатуры. На
    настоящем `disabled` `document.activeElement` остался бы на `<body>`, и
    человек с клавиатуры (и с программой чтения) не узнал бы, что функция есть."""
    assert blocked["disabledAttribute"] is None, "кнопка снова настоящий disabled"
    assert blocked["ariaDisabled"] == "true", blocked
    assert blocked["focused"], (
        "кнопка не взялась фокусом: с клавиатуры до неё не дойти")


def test_pressing_it_shows_the_missing_permission_on_the_screen(blocked):
    assert blocked["noticeBefore"] == "", (
        f"плашка висит до нажатия: {blocked['noticeBefore']!r}")
    assert blocked["noticeBeforeBox"]["h"] == 0, (
        f"пустая плашка занимает место: {blocked['noticeBeforeBox']}")
    assert "недоступно" in blocked["noticeAfter"], blocked["noticeAfter"]
    assert "Создание и изменение товаров" in blocked["noticeAfter"], blocked
    assert "Изменение цен" in blocked["noticeAfter"], (
        "плашка молчит про цену, а человек упрётся в отказ API: " + blocked["noticeAfter"])
    assert "products.edit" not in blocked["noticeAfter"], (
        "плашка называет код права, а человек на код не смотрит")
    assert blocked["noticeAfterBox"]["h"] > 20, (
        f"плашка не появилась на экране: {blocked['noticeAfterBox']}")
    assert blocked["closer"] == "Скрыть", "плашку нечем закрыть"
    assert blocked["modalOpen"] == "false", (
        "недоступная кнопка открыла форму создания")
    assert blocked["errors"] == [], blocked["errors"]


def test_the_add_row_button_really_adds_a_row(added):
    """Главная проверка пункта «Добавить строку»: на настоящей странице кнопка
    добавляет строку. Проверка «кнопка есть в разметке» прошла бы на странице,
    где по ней нельзя нажать."""
    assert added["found"], "на странице нет кнопки «Добавить строку» вовсе"
    assert added["before"] == 1, f"изначально строк не одна: {added}"
    assert added["after"] == 3, (
        f"два нажатия дали {added['after']} строк вместо трёх: {added}")
    assert added["errors"] == [], (
        "страница бросила ошибку скрипта: " + "; ".join(added["errors"]))
    assert added["pickers"] == 3 and added["cells"] == 3, (
        f"добавленные строки без подстановки: {added}")


def test_the_add_row_button_does_not_call_a_function_that_is_not_there(added):
    """Причина, найденная в консоли: `onclick="addItemRow()"` ищет функцию в
    глобальной области, а она объявлена внутри IIFE страницы. Кнопка звала
    `addItemRow is not defined`, и нажатие ничего не делало. Поэтому обработчик
    висит на форме, как у остальных кнопок подстановки."""
    assert added["inlineHandler"] is None, (
        f"кнопка снова зовёт inline-обработчик: {added['inlineHandler']!r}")
    assert added["defined"] == "undefined", (
        "функция addItemRow снова в глобальной области — проверяем не то")