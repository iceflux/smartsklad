"""Генератор демо-данных в формате импорта из 1С.

Пишет .json ровно того формата, который читает app.modules.importer.files.read_payloads,
поэтому демо-данные идут в приложение тем же путём, что и настоящая выгрузка 1С, и нового
пути импорта не появляется. Скрипт только пишет файл: ни одного запроса в базу он не делает,
запись в неё принадлежит исключительно экрану импорта приложения.

Единицы берутся из DEFAULT_UNITS приложения: любая единица вне справочника остановила бы
импорт экраном решения, и демо не запустилось бы с первого раза. Окно прогноза берётся из
модуля прогноза (LOOKBACK_DAYS), чтобы демо-каталог продавался ровно в том диапазоне, который
смотрит /forecast. Даты заказов отсчитываются от текущего дня, поэтому при одинаковом --seed
файл воспроизводится байт в байт в течение дня.

Форма продаж повторяет складскую: пятая часть каталога продаётся регулярно, с трендом и
недельной сезонностью, у части этих товаров пустой склад (прогноз выдаёт «к закупке»), а
остальной каталог не продаётся почти вовсе — он и даёт контраст состоянию «мало данных».

Запускается тем же интерпретатором, что и приложение, из корня репозитория:

    .\\.venv\\Scripts\\python.exe scripts\\gen_demo.py --products 200 --customers 60 \\
        --orders 400 --out .system_folder/1c_export/demo.json --seed 1

Затем файл импортируется как обычно: /import, «Разобрать», «Импортировать».
"""

import argparse
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.ai.service import LOOKBACK_DAYS  # noqa: E402
from app.modules.inventory.service import DEFAULT_UNITS  # noqa: E402

PRODUCTS = (
    ("Гвоздь", False, ("шт", "кг", "упак", "ящ", "пач")),
    ("Саморез", False, ("шт", "кг", "упак", "ящ", "пач")),
    ("Шуруп", False, ("шт", "кг", "упак", "пач")),
    ("Болт", False, ("шт", "кг", "ящ")),
    ("Гайка", True, ("шт", "кг", "пач", "ящ")),
    ("Шайба", True, ("шт", "кг", "пач", "ящ")),
    ("Шпилька", True, ("шт", "кг", "пач")),
    ("Штифт", False, ("шт", "кг", "пач")),
    ("Дюбель", False, ("шт", "упак", "пач")),
    ("Анкер", False, ("шт", "упак", "ящ")),
    ("Провод", False, ("м", "м2")),
    ("Кабель", False, ("м", "м2")),
    ("Труба", True, ("м", "т")),
    ("Профиль", False, ("м", "шт", "компл")),
    ("Арматура", True, ("м", "т", "шт")),
    ("Уголок", False, ("м", "т", "шт")),
    ("Лист", False, ("м2", "шт", "компл")),
    ("Полоса", True, ("м", "т", "шт")),
    ("Пруток", False, ("м", "т", "шт")),
    ("Круг", False, ("м", "т")),
    ("Шланг", False, ("м", "пач", "рул")),
    ("Трос", False, ("м", "рул", "кг")),
)
VARIANTS = (
    "оцинкованный", "нержавеющий", "стальной", "латунный", "медный", "алюминиевый",
    "полиэфирный", "полипропиленовый", "серебристый", "чёрный", "белый", "прозрачный",
)
VARIANTS_FEMININE = (
    "оцинкованная", "нержавеющая", "стальная", "латунная", "медная", "алюминиевая",
    "полиэфирная", "полипропиленовая", "серебристая", "чёрная", "белая", "прозрачная",
)
SIZES = ("3х70", "4х40", "5х100", "6х60", "8х80", "10х100", "12", "16", "20", "25", "32", "50")
SURNAMES = (
    ("Иванов", "Иванова"), ("Петров", "Петрова"), ("Сидоров", "Сидорова"),
    ("Кузнецов", "Кузнецова"), ("Смирнов", "Смирнова"), ("Попов", "Попова"),
    ("Лебедев", "Лебедева"), ("Козлов", "Козлова"), ("Новиков", "Новикова"),
    ("Морозов", "Морозова"), ("Волков", "Волкова"), ("Зайцев", "Зайцева"),
    ("Павлов", "Павлова"), ("Семёнов", "Семёнова"), ("Голубев", "Голубева"),
    ("Виноградов", "Виноградова"), ("Богданов", "Богданова"), ("Воробьёв", "Воробьёва"),
    ("Фёдоров", "Фёдорова"), ("Михайлов", "Михайлова"),
)
GIVEN = (
    (("Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Артём", "Илья",
      "Кирилл", "Николай", "Павел", "Роман", "Егор", "Арсений"),
     ("Александровна", "Дмитриевна", "Максимовна", "Сергеевна", "Андреевна", "Алексеевна",
      "Артёмовна", "Ильинична", "Кирилловна", "Николаевна", "Павловна", "Романовна",
      "Егоровна", "Арсеньевна")),
    (("Ольга", "Елена", "Анна", "Татьяна", "Наталья", "Светлана", "Ирина", "Юлия",
      "Екатерина", "Мария", "Дарья", "Ксения", "Полина", "Вера"),
     ("Ольговна", "Еленовна", "Анновна", "Татьяновна", "Натальевна", "Светлановна",
      "Ириновна", "Юлиевна", "Екатериновна", "Мариевна", "Дарьевна", "Ксениевна",
      "Полиновна", "Веровна")),
)
COMPANIES = (
    ("ООО", "Ромашка"), ("ООО", "Восход"), ("ООО", "Прогресс"), ("ООО", "Магнат"),
    ("АО", "Кристалл"), ("АО", "Вектор"), ("ЗАО", "Гранит"), ("ЗАО", "Титан"),
    ("ИП", "Ковалёв"), ("ИП", "Ерохин"), ("ООО", "Оптима"), ("ООО", "Сфера"),
    ("ООО", "Меридиан"), ("ПАО", "Факел"), ("ПАО", "Заря"),
)
ORDER_DAYS = 180
ORDER_HOURS = ("09", "10", "11", "13", "14", "15", "16", "17", "18", "19")
MAX_LINES = 6
MAX_QUANTITY = 25
MIN_REST = 50
MAX_REST = 500
DEFAULT_PRODUCTS = 200
DEFAULT_CUSTOMERS = 60
DEFAULT_ORDERS = 400
DEFAULT_SEED = 1
# Продающийся товар. Демо-каталог повторяет форму настоящего склада: хвост из
# редких позиций и несколько ходовых. Прогнозу спроса нужны не все товары, а
# те, у которых есть продажи; остальные дают контраст состояния «мало данных».
MOVER_SHARE = 0.2
# Продажи раскидываются по заказам кусками не больше MAX_LINES, поэтому заказа
# на день выходит меньше, чем событий: бюджет считается в строках, а не в
# заказах, иначе движущиеся товары не добирали бы дней с продажами.
MOVER_LINE_SHARE = 0.6
# Дней с продажами у движущегося товара должно быть заведомо больше порога
# прогноза, иначе демо снова покажет сплошные прочерки.
MIN_SELLING_DAYS = 20
# Запас ходового товара: часть товаров намеренно с пустым складом (их прогноз
# выдаёт «к закупке»), часть с полным (прогноз есть, а покупать не надо).
MOVER_LOW_REST = (1, 40)
MOVER_HIGH_REST = (200, 600)
# Недельная сезонность, Пн..Вс: в субботу и воскресенье склад отдыхает.
WEEKLY = (0.55, 0.8, 1.0, 1.1, 1.35, 0.6, 0.25)


def _digits(rng: random.Random, length: int) -> str:
    return rng.choice("123456789") + "".join(rng.choice("0123456789") for _ in range(length - 1))


def _products(rng: random.Random, count: int) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for index in range(1, count + 1):
        noun, feminine, preferred = PRODUCTS[rng.randrange(len(PRODUCTS))]
        allowed = [name for name in preferred if name in DEFAULT_UNITS]
        variants = VARIANTS_FEMININE if feminine else VARIANTS
        name = f"{noun} {variants[rng.randrange(len(variants))]} {SIZES[rng.randrange(len(SIZES))]}"
        if name in seen:
            name = f"{name} №{index}"
        seen.add(name)
        rows.append({
            "id": f"p{index}",
            "name": name,
            "unit": allowed[rng.randrange(len(allowed))],
            "price": round(rng.uniform(15, 4800), 2),
            "rest": rng.randrange(MIN_REST, MAX_REST),
        })
    return rows


def _customers(rng: random.Random, count: int) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for index in range(1, count + 1):
        if rng.random() < 0.5:
            male, female = SURNAMES[rng.randrange(len(SURNAMES))]
            male_given, female_given = GIVEN[0]
            male_patronymic, female_patronymic = GIVEN[1]
            if rng.random() < 0.5:
                name = f"{male} {male_given[rng.randrange(len(male_given))]} {male_patronymic[rng.randrange(len(male_patronymic))]}"
            else:
                name = f"{female} {female_given[rng.randrange(len(female_given))]} {female_patronymic[rng.randrange(len(female_patronymic))]}"
            inn = _digits(rng, 12)
        else:
            kind, word = COMPANIES[rng.randrange(len(COMPANIES))]
            name = f"{kind} {word}"
            inn = _digits(rng, 10)
        if name in seen:
            name = f"{name} №{index}"
        seen.add(name)
        rows.append({"id": f"c{index}", "name": name, "inn": inn})
    return rows


def _orders(rng: random.Random, count: int, products: list[dict],
            customers: list[dict]) -> list[dict]:
    """Заказы: сначала продажи ходовых товаров, потом фон по всему каталогу.

    Порядок важен: продажи считаются по дням и упаковываются в заказы кусками не
    больше MAX_LINES, поэтому они и дают товару нужное число ДНЕЙ с продажами, а
    не просто число строк. Остальные заказы добираются до запрошенного числа и
    размазаны по ORDER_DAYS: они дают длинные редкие продажи хвоста каталога.
    """
    today = date.today()
    by_id = {row["id"]: row for row in products}
    product_ids = list(by_id)
    customer_ids = [row["id"] for row in customers]
    rows: list[dict] = []
    index = 0

    movers = rng.sample(products, min(len(products), max(1, round(len(products) * MOVER_SHARE))))
    line_budget = max(len(movers) * MIN_SELLING_DAYS, int(count * MAX_LINES * MOVER_LINE_SHARE))
    per_mover = line_budget // max(1, len(movers))
    probability = min(0.7, per_mover / LOOKBACK_DAYS)
    by_day: dict[date, list[tuple[dict, int]]] = {}
    for mover in movers:
        mover["rest"] = rng.randint(*(MOVER_LOW_REST if rng.random() < 0.6 else MOVER_HIGH_REST))
        base = rng.uniform(0.8, 4.0)
        trend = rng.uniform(-0.4, 0.5)
        for back in range(LOOKBACK_DAYS):
            if rng.random() >= probability:
                continue
            day = today - timedelta(days=back)
            level = base * (1 + trend * (LOOKBACK_DAYS - 1 - back) / LOOKBACK_DAYS)
            level *= WEEKLY[day.weekday()]
            by_day.setdefault(day, []).append((mover, max(1, round(level * rng.uniform(0.55, 1.45)))))

    for day in sorted(by_day, reverse=True):
        lines = by_day[day]
        rng.shuffle(lines)
        for start in range(0, len(lines), MAX_LINES):
            if len(rows) >= count:
                break
            index += 1
            rows.append(_order(rng, day, lines[start:start + MAX_LINES], customer_ids, index))
        if len(rows) >= count:
            break

    while len(rows) < count:
        index += 1
        day = today - timedelta(days=rng.randrange(ORDER_DAYS))
        picks = rng.sample(product_ids, min(rng.randint(1, 3), len(product_ids)))
        lines = [(by_id[pid], rng.randint(1, MAX_QUANTITY)) for pid in picks]
        rows.append(_order(rng, day, lines, customer_ids, index))
    return rows


def _order(rng: random.Random, day: date, lines: list[tuple[dict, int]],
           customer_ids: list[str], index: int) -> dict:
    moment = datetime(day.year, day.month, day.day,
                      int(ORDER_HOURS[rng.randrange(len(ORDER_HOURS))]),
                      rng.randrange(60), rng.randrange(60))
    row = {
        "id": f"o{index}",
        "number": f"0000-{index:06d}",
        "date": moment.strftime("%Y-%m-%d %H:%M:%S"),
        "items": [
            {
                "product_id": product["id"],
                "name": product["name"],
                "unit": product["unit"],
                "quantity": quantity,
                "price": product["price"],
            }
            for product, quantity in lines
        ],
    }
    if customer_ids:
        row["customer_id"] = customer_ids[rng.randrange(len(customer_ids))]
    return row


def build_payload(products: int, customers: int, orders: int, seed: int) -> dict:
    """Собирает разделы products, customers и orders без обращения к базе."""
    rng = random.Random(seed)
    product_rows = _products(rng, products)
    customer_rows = _customers(rng, customers)
    return {
        "products": product_rows,
        "customers": customer_rows,
        "orders": _orders(rng, orders, product_rows, customer_rows),
    }


def write_payload(payload: dict, out: str | Path) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сгенерировать демо-данные .json в формате импорта из 1С. "
                    "Файл потом импортируется через /import как обычная выгрузка 1С. "
                    "Базу данных скрипт не трогает.")
    parser.add_argument("--products", type=int, default=DEFAULT_PRODUCTS, help="сколько товаров")
    parser.add_argument("--customers", type=int, default=DEFAULT_CUSTOMERS, help="сколько клиентов")
    parser.add_argument("--orders", type=int, default=DEFAULT_ORDERS, help="сколько заказов")
    parser.add_argument("--out", required=True, help="путь к .json, который потом положите "
                                                     "в папку импорта, например "
                                                     ".system_folder/1c_export/demo.json (обязателен)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="зерно: один seed даёт один и тот же файл в пределах одного дня, "
                             "потому что даты заказов считаются от сегодняшнего")
    args = parser.parse_args(argv)

    payload = build_payload(args.products, args.customers, args.orders, args.seed)
    path = write_payload(payload, args.out)
    unknown = sorted({row["unit"] for row in payload["products"]} - set(DEFAULT_UNITS))
    print(f"{path}: товаров {len(payload['products'])}, клиентов {len(payload['customers'])}, "
          f"заказов {len(payload['orders'])}, единиц вне справочника: "
          f"{', '.join(unknown) if unknown else 'нет'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
