"""Генератор демо-данных в формате импорта из 1С.

Пишет .json ровно того формата, который читает app.modules.importer.files.read_payloads,
поэтому демо-данные идут в приложение тем же путём, что и настоящая выгрузка 1С, и нового
пути импорта не появляется. Скрипт только пишет файл: ни одного запроса в базу он не делает,
запись в неё принадлежит исключительно экрану импорта приложения.

Единицы берутся из DEFAULT_UNITS приложения: любая единица вне справочника остановила бы
импорт экраном решения, и демо не запустилось бы с первого раза. Даты заказов отсчитываются
от текущего дня, поэтому при одинаковом --seed файл воспроизводится байт в байт в течение дня.

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
    today = date.today()
    product_ids = [row["id"] for row in products]
    by_id = {row["id"]: row for row in products}
    customer_ids = [row["id"] for row in customers]
    rows = []
    for index in range(1, count + 1):
        day = today - timedelta(days=rng.randrange(ORDER_DAYS))
        moment = datetime(int(day.year), int(day.month), int(day.day),
                          int(ORDER_HOURS[rng.randrange(len(ORDER_HOURS))]),
                          rng.randrange(60), rng.randrange(60))
        lines = []
        for product_id in rng.sample(product_ids, min(rng.randint(1, MAX_LINES), len(product_ids))):
            product = by_id[product_id]
            lines.append({
                "product_id": product_id,
                "name": product["name"],
                "unit": product["unit"],
                "quantity": rng.randint(1, MAX_QUANTITY),
                "price": product["price"],
            })
        row = {
            "id": f"o{index}",
            "number": f"0000-{index:06d}",
            "date": moment.strftime("%Y-%m-%d %H:%M:%S"),
            "items": lines,
        }
        if customer_ids:
            row["customer_id"] = customer_ids[rng.randrange(len(customer_ids))]
        rows.append(row)
    return rows


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
