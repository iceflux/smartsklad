"""Печать заказа одним кликом с самой страницы заказа.

`window.print()` печатает ТЕКУЩИЙ документ, поэтому документ должен лежать в
странице заказа — иначе печать стоит двух нажатий (ссылка, потом Ctrl+P). Схема
«перейти на предпросмотр и там печатать» отвергнута: после загрузки страницы
жеста пользователя нет, и браузер вправе не показать окно печати.

Проверки здесь четыре, и все четверо смотрят на разные вещи, которые нельзя
проверить друг за друга:

* разметку документа — сравнением с предпросмотром (одна копия разъехаться не
  может, но разъехаться может контекст: страница заказа отдаёт в документ не
  те значения);
* область печати — замером в настоящем Chrome, потому что «экранное правило
  скрывает» проверяется только вычисленным `display`;
* область действия — страницей БЕЗ метки: иначе скрытие оболочки напечатало бы
  почти пустой лист на каждой странице приложения;
* кнопку — разбором обработчика, потому что headless Chrome печать не
  показывает (см. `docs/test_checklist.md`, раздел про печать).
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

from tests.test_printing import (  # noqa: F401  — фикстура `env` нужна и здесь
    _admin, _body_mark, _elements, _matches, _print_hides, env,
)

ROOT = Path(__file__).resolve().parents[1]
BASE_HTML = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
BUILT_CSS = ROOT / "app" / "static" / "app.css"

# Область печати — ровно то, что печатается. Границы ищутся по МЕТКЕ, а не по
# содержимому: содержимое и есть предмет сравнения, и искать его по нему —
# значит сравнивать документ с самим собой.
REGION = re.compile(r'<div[^>]*\bdata-print-document-region\b[^>]*>\n(.*?)\n</div>', re.S)

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)

INNER = """<!doctype html><html><head><meta charset="utf-8">
<style>__CSS__</style>
<script>
/* Ошибка скрипта страницы иначе видна только пустым выводом: прибор молчал бы,
   и падение выглядело бы как «Chrome ничего не измерил». */
window.onerror = function (message, source, line) {
  document.title = 'ERROR ' + message + ' @' + line;
};
window.addEventListener('unhandledrejection', function (event) {
  var reason = event.reason;
  document.title = 'ERROR promise: ' + (reason && (reason.message || reason));
});
</script></head>
<body class="bg-surface text-primary" data-sidebar="expanded" data-nav="closed">
__BODY__
<script>
var region = document.querySelector('[data-print-document-region]');
var style = region && window.getComputedStyle(region);
document.title = 'RESULT' + JSON.stringify({
  found: !!region,
  display: style ? style.display : null,
  height: region ? Math.round(region.getBoundingClientRect().height) : null,
  visible: region ? !!(region.offsetWidth || region.offsetHeight) : null,
}) + 'END';
</script>
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
  if (!d) { return; }
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


def _chrome() -> str | None:
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _region(html: str) -> str:
    found = REGION.findall(html)
    assert len(found) == 1, f"область печати найдена {len(found)} раз, а нужна одна"
    assert found[0].strip(), "область печати пуста"
    return found[0]


def _measure(body: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="print-region-"))
    inner = work / "inner.html"
    inner.write_text(
        INNER.replace("__CSS__", BUILT_CSS.read_text(encoding="utf-8"))
              .replace("__BODY__", body),
        encoding="utf-8")
    outer = work / "outer.html"
    outer.write_text(OUTER.replace("__INNER__", inner.resolve().as_uri()), encoding="utf-8")
    # `--allow-file-access-from-files` обязателен: каждый file:// в Chrome — свой
    # непрозрачный origin, и без флага contentDocument из родителя недостижим.
    shell = (f'"{_chrome()}" --headless=new --disable-gpu --no-sandbox '
             f'--allow-file-access-from-files --window-size=1600,1400 '
             f'--virtual-time-budget=8000 --dump-dom "{outer.resolve().as_uri()}"')
    out = subprocess.run(shell, shell=True, capture_output=True, text=True, encoding="utf-8")
    match = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.DOTALL)
    assert match, out.stdout[-3000:] + out.stderr[-2000:]
    raw = html_mod.unescape(match.group(1))
    assert raw.startswith("RESULT"), repr(raw[:400])
    return json.loads(raw[len("RESULT"):-len("END")])


@pytest.fixture(scope="module")
def chrome_available() -> bool:
    return _chrome() is not None


def test_the_order_page_prints_the_same_document_as_the_preview(client, db_session, env):
    """Одна копия разметки, но контексты у страниц разные.

    Общего куска `documents/order_document.html` мало: страница заказа отдаёт в
    документ значения СВОИМ роутом, и забытое поле показало бы пустой блок
    продавца или ноль в итоге. Сравнение целиком ловит и это, и правку одной из
    копий, если кто-то всё-таки скопирует разметку вместо включения куска.
    """
    _admin(client, db_session)
    _, _, order = env
    on_order = _region(client.get(f"/orders/{order.id}").text)
    on_preview = _region(client.get(f"/orders/{order.id}/print").text)
    assert on_order == on_preview
    for expected in ("Заказ покупателя", "ООО Строй", "Гвозди", "Итого:"):
        assert expected in on_order, f"в документе страницы заказа нет {expected!r}"


def test_the_order_page_hides_the_shell_but_a_page_without_the_document_does_not(client, db_session, env):
    """Область действия скрытия — ровно страницы с меткой на `<body>`.

    Без второй половины проверка была бы проверкой наполовину: правило без метки
    спрятало бы оболочку на каждой странице, и Ctrl+P везде печатал бы почти
    пустой лист. Список заказов — страница без метки и без документа, то есть
    ровно тот случай, который обязан печататься сам.
    """
    _admin(client, db_session)
    _, _, order = env
    rules = _print_hides()
    assert rules, "в собранном CSS нет ни одного правила, прячущего что-то на печати"

    def shell(page):
        return [e for e in _elements(page)
                if e[0] in ("header", "aside") or e[1] & {"app-sidebar", "app-overlay"}]

    on_order = client.get(f"/orders/{order.id}").text
    assert _body_mark(on_order) == "true"
    order_shell = shell(on_order)
    assert order_shell, "на странице заказа нет ни шапки, ни панели — проверять нечего"
    uncovered = [e for e in order_shell
                 if not any(_matches(e, rule, "true") for rule in rules)]
    assert uncovered == [], f"эти части оболочки попадут на бумагу вместе с документом: {uncovered}"

    on_list = client.get("/orders").text
    assert _body_mark(on_list) is None, (
        "метка печати стоит на странице без документа — оболочка скрылась бы везде"
    )
    list_shell = shell(on_list)
    assert list_shell, "в списке заказов нет ни шапки, ни панели — проверять нечего"
    hidden = [e for e in list_shell if any(_matches(e, rule, None) for rule in rules)]
    assert hidden == [], f"эти части оболочки спрятались на странице без документа: {hidden}"


def test_the_document_is_hidden_on_screen_on_the_order_page(client, db_session, env, chrome_available):
    """Дубль документа на странице заказа не должен быть виден на экране.

    Проверка на исходнике и на артефакте сборки «экранное правило есть» —
    проверка наполовину: правило может вылететь из сборки, и страница покажет
    второй заказ под первым. Поэтому здесь настоящий Chrome и ВЫЧИСЛЕННЫЙ
    `display`, а для контраста — тот же замер на предпросмотре, где документ
    виден обязан: иначе проверка прошла бы и при правиле, прячущем документ
    везде.
    """
    if not chrome_available:
        pytest.skip("Chrome не установлен")
    _admin(client, db_session)
    _, _, order = env
    css = BUILT_CSS.read_text(encoding="utf-8")

    def body_of(page):
        return page[page.index("<body"): page.index("</body>")]

    on_order = _measure(body_of(client.get(f"/orders/{order.id}").text))
    assert on_order["found"], "в разметке страницы заказа нет области печати"
    assert on_order["display"] == "none", (
        f"область печати видна на экране страницы заказа: {on_order}"
    )
    assert on_order["visible"] is False and on_order["height"] == 0, on_order

    on_preview = _measure(body_of(client.get(f"/orders/{order.id}/print").text))
    assert on_preview["found"], "в разметке предпросмотра нет области печати"
    assert on_preview["display"] != "none", (
        f"предпросмотр печати стал пустым на экране: {on_preview}"
    )
    assert css.count("body[data-print-document] .app-print-only") == 2, (
        "ожидались два правила: скрыть на экране и показать на бумаге"
    )


def test_the_print_button_prints_on_the_click_and_without_javascript_opens_the_preview(client, db_session, env):
    """Кнопка печати: жест пользователя в обработчике, без скрипта — предпросмотр.

    `window.print()` обязан звать Обработчик нажатия: после загрузки страницы
    жеста нет, и браузер вправе не показать окно печати. Проверка смотрит и на
    вызов, и на `preventDefault` — без него ссылка успела бы уйти на предпросмотр
    вместе с печатью.

    Без скрипта кнопка обязана делать что-то осмысленное, и это сам предпросмотр:
    пустая кнопка при выключенном JavaScript выглядела бы как поломка.
    """
    _admin(client, db_session)
    _, _, order = env
    html = client.get(f"/orders/{order.id}").text
    button = re.search(r'<a\b[^>]*\bdata-print-now\b[^>]*>', html)
    assert button, "на странице заказа нет кнопки печати одним кликом"
    assert f'href="/orders/{order.id}/print"' in button.group(0), (
        "без скрипта кнопка печати никуда не ведёт: " + button.group(0)
    )

    handler = re.search(
        r'document\.querySelector\("\[data-print-now\]"\)(.*?)\}\);', BASE_HTML, re.S)
    assert handler, "в base.html нет обработчика кнопки печати одним кликом"
    body = handler.group(1)
    assert "window.print()" in body, "обработчик не зовёт window.print()"
    assert "preventDefault" in body, (
        "обработчик не отменяет переход по ссылке: печать плюс предпросмотр"
    )
    assert f'addEventListener("click"' in body, "обработчик не на нажатии"