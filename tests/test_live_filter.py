"""Живая фильтрация на страницах разделов: список перерисовывается, пока
человек печатает, а не по Enter или кнопке «Фильтр».

Пауза живёт в браузере, и это решение, а не умолчание: страницы уже фильтруют
ровно этим же `search=` через `list_customers`/`list_products`/`list_orders`, то
есть серверная пауза не изменила бы ни одного запроса, а добавила бы состояние
на пользователя. Побочная и нужная вещь: без скрипта форма по-прежнему
работает по Enter и по кнопке, то есть страница не стала зависеть от JS там,
где раньше не зависела.

Один скрипт на все три страницы — в `base.html`. Три копии означали бы, что
правка паузы или поля идёт в трёх местах, и рано или поздно разъедется.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
BASE = TEMPLATES / "base.html"

SECTION_PAGES = ("products.html", "customers.html", "orders.html")

FORM = re.compile(r"<form[^>]*data-live-filter[^>]*>")
LIST = re.compile(r'data-live-list')


def test_every_section_filter_form_asks_for_live_filtering():
    missing = [name for name in SECTION_PAGES if not FORM.search((TEMPLATES / name).read_text(encoding="utf-8"))]
    assert missing == [], f"нет живой фильтрации на {missing}"


def test_the_list_the_filter_replaces_is_marked():
    missing = [name for name in SECTION_PAGES if not LIST.search((TEMPLATES / name).read_text(encoding="utf-8"))]
    assert missing == [], f"список на {missing} не помечен, и пауза не скажет, что он в работе"


def test_the_live_filter_script_lives_in_one_place():
    copies = [name for name in SECTION_PAGES
              if "liveFilterDebounce" in (TEMPLATES / name).read_text(encoding="utf-8")]
    assert copies == [], f"скрипт размножен по страницам {copies}, он живёт в base.html"
    assert BASE.read_text(encoding="utf-8").count("liveFilterDebounce") >= 1


def test_the_live_filter_waits_three_hundred_milliseconds():
    body = BASE.read_text(encoding="utf-8")
    match = re.search(r"DEBOUNCE_MS\s*=\s*(\d+)", body)
    assert match, "в base.html нет паузы у живой фильтрации"
    assert 250 <= int(match.group(1)) <= 400, (
        f"пауза {match.group(1)} мс: на 6-7 буквах слова её либо не чувствуешь, "
        "либо ждёшь слишком долго")


def _depth_at(text: str, index: int) -> int:
    """Сколько `<div>` ещё не закрыто перед позицией `index`."""
    head = text[:index]
    return len(re.findall(r"<div\b", head)) - len(re.findall(r"</div>", head))


def test_the_marked_region_covers_the_total_and_the_pager():
    """`data-live-list` — это то, что перерисовывается по нажатию. Раньше пометка
    лежала на обёртке таблицы, а «Всего: N» и «Назад/Вперёд» стояли рядом, но вне
    её: список отфильтровался бы, а счётчик и постраничные ссылки показывали бы
    прежние числа. Пометка поэтому поднята выше — на внешний блок."""
    for name in SECTION_PAGES:
        body = (TEMPLATES / name).read_text(encoding="utf-8")
        opened = body.index("data-live-list")
        for label in ("Всего:", "Вперёд"):
            index = body.index(label)
            assert _depth_at(body, index) > _depth_at(body, opened), (
                f"{name}: «{label}» вне перерисовываемого блока — счётчик и "
                "постраничные ссылки остались бы от прежнего фильтра")


def test_the_live_filter_does_not_navigate_the_page():
    """Отправка формы перезагружает страницу, и фокус уходит из поля на первом же
    символе — с этого начался дефект. Значит скрипт обязан брать список себе и
    подставлять его на месте."""
    base = BASE.read_text(encoding="utf-8")
    block = base[base.index("liveFilterDebounce") - 400:]
    assert "requestSubmit" not in block, (
        "живая фильтрация снова отправляет форму: фокус уходит после первой буквы")
    assert "DOMParser" in block and "list.innerHTML" in block, (
        "список не подставляется на месте, а значит страница уезжает целиком")
    """Без скрипта страница обязана остаться рабочей: та же самая форма, тот же
    вход `search`. Иначе «живая фильтрация» стала бы требованием JS там, где
    раньше его не было."""
    for name in SECTION_PAGES:
        body = (TEMPLATES / name).read_text(encoding="utf-8")
        form = re.search(r"<form[^>]*data-live-filter[^>]*>([\s\S]*?)</form>", body)
        assert form, name
        assert '<input name="search"' in form.group(1) or \
            '<input type="search" name="search"' in form.group(1), (
                f"{name}: в форме живой фильтрации нет поля search, и без скрипта "
                "страница фильтровать нечем")
        assert "Фильтр" in body, f"{name}: без скрипта нечем отправить форму"
