"""Сторож: ни одно сообщение, видимое человеку, не осталось на английском.

Почему именно свип, а не список строк. Перевод — это про «осталось», а остаётся
не то, что уже перевели, а то, что забыли: новый модуль, новая проверка, чужой
снимок кода. Список переведённых строк через год врал бы, показывая зелёный
прогон при новом английском сообщении рядом. Свип ловит именно это, а список
переведённых строк — нет.

Что попадает под проверку и почему только это:

* `raise <Исключение>(...)` — сообщение, которое долетает до страницы через
  `str(e)` в редиректе `?msg=` или до человека через 422 с `detail`;
* `HTTPException(..., detail=...)` — то же, но в API.

Что НЕ попадает и не должно:

* `models.py` — там нет сообщений, только имена колонок;
* `exception text used for control flow` — например `httpx`/`smtplib`, их текст
  пишется в журнал как есть, переводить его нечем;
* сравнение строк с литералом: подмена такого текста молча ломает проверку, и
  найти её можно только этим свипом. Ни одно сообщение в `app` не сравнивается
  со своим же текстом — единственное сравнение (`UNTRANSLATED` в справке) живёт
  в тестах, и оно заменено обратным утверждением.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "app"

RAISE = re.compile(r"raise\s+\w+(?:\([^)]*?)?[\"']([^\"']*)[\"']")
DETAIL = re.compile(r"detail=(?:f)?[\"']([^\"']*)[\"']")
CYRILLIC = re.compile(r"[\u0400-\u04ff]")


def _messages(only_foreign: bool = True):
    """(путь, номер строки, текст) для каждого найденного сообщения.

    `only_foreign=True` оставляет только те, где нет кириллицы, — их переводить
    и надо. `False` отдаёт все, что нашлось, — этим пользуется проверка охвата,
    которой нужно знать, что файл вообще просмотрен.
    """
    found = []
    for path in sorted(APP.rglob("*.py")):
        if path.name == "models.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in (RAISE, DETAIL):
                for match in pattern.finditer(line):
                    text = match.group(1)
                    if text and not (only_foreign and CYRILLIC.search(text)):
                        found.append((path.relative_to(ROOT).as_posix(), number, text))
    return found


def test_no_user_visible_message_is_left_in_english():
    offenders = _messages()
    assert not offenders, "сообщения, видимые человеку, остались на английском:\n" + "\n".join(
        f"  {path}:{number}: {text!r}" for path, number, text in offenders)


def test_the_sweep_actually_finds_something_when_there_is_something_to_find():
    """Охрана охраны.

    Свип, который ничего не находит, и свип, который ничего не умеет искать,
    выглядят одинаково. Подстановки требуют, чтобы свип назвал то, что подставили:
    без этой проверки «пусто» означало бы «чисто» даже при сломанном самом свипе.
    """
    sample = 'raise ValueError("some english message")'
    assert RAISE.search(sample).group(1) == "some english message"
    detail = 'raise HTTPException(status_code=422, detail="value must be 5")'
    assert DETAIL.search(detail).group(1) == "value must be 5"
    assert not CYRILLIC.search(RAISE.search(sample).group(1))
    assert CYRILLIC.search(RAISE.search('raise ValueError("скидка")').group(1))


SWEEPT_MODULES = (
    "app/modules/customers/service.py",
    "app/modules/inventory/service.py",
    "app/modules/notifications/service.py",
    "app/modules/notifications/telegram.py",
    "app/modules/notifications/router.py",
    "app/modules/auth/utils.py",
    "app/modules/backup/service.py",
    "app/modules/settings/router.py",
    "app/main.py",
)
"""Модули, где английские сообщения были или где такие появляются.

Список нужен не для красоты: сузить охват свипа — значит получить зелёный прогон
на неполной выборке, и заметил бы это только человек, которому снова показали
английский отказ. В списке только файлы, где свип что-то находит: `units_router.py`
сообщений не содержит и подменяет их сервисными.
"""


def test_the_sweep_reaches_every_module_that_carries_messages():
    covered = {path for path, _number, _text in _messages(only_foreign=False)}
    for module in SWEEPT_MODULES:
        assert module in covered, f"{module} выпал из обхода свипа"
    assert len(covered) >= 15, (
        f"свип нашёл сообщения в {len(covered)} файлах, а не в 15 и больше: обход "
        "сузился, и «ничего не осталось» означает «ничего не искали»")