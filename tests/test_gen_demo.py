import json
import random
from datetime import datetime
from pathlib import Path

from app.modules.ai.service import forecast_rows
from app.modules.audit.models import AuditLog
from app.modules.customers.models import Customer
from app.modules.importer.orders import DATE_FORMATS, parse_order_date
from app.modules.importer.service import plan_import, run_import
from app.modules.inventory.models import Product
from app.modules.inventory.service import DEFAULT_UNITS
from app.modules.orders.models import Order
from app.modules.orders.service import create_order
from scripts import gen_demo
from scripts.gen_demo import (
    DEFAULT_CUSTOMERS,
    DEFAULT_ORDERS,
    DEFAULT_PRODUCTS,
    DEFAULT_SEED,
    GIVEN,
    PRODUCTS,
    SIZES,
    SURNAMES,
    VARIANTS,
    VARIANTS_FEMININE,
    build_payload,
    main,
    write_payload,
)

SEED = 7


def _file(tmp_path, products=40, customers=15, orders=60, seed=SEED, name="demo.json"):
    payload = build_payload(products, customers, orders, seed)
    return write_payload(payload, tmp_path / name)


def test_generator_is_reproducible_for_one_seed(tmp_path):
    first = _file(tmp_path, name="a.json").read_bytes()
    second = _file(tmp_path, name="b.json").read_bytes()
    assert first == second


def test_another_seed_gives_another_file(tmp_path):
    first = _file(tmp_path, seed=1, name="a.json").read_bytes()
    second = _file(tmp_path, seed=2, name="b.json").read_bytes()
    assert first != second


def test_written_file_is_the_three_sections(tmp_path):
    payload = json.loads(_file(tmp_path).read_bytes().decode("utf-8"))
    assert sorted(payload) == ["customers", "orders", "products"]
    assert len(payload["products"]) == 40
    assert len(payload["customers"]) == 15
    assert len(payload["orders"]) == 60


def test_every_unit_comes_from_the_app_catalogue(tmp_path):
    payload = json.loads(_file(tmp_path).read_bytes().decode("utf-8"))
    used = {row["unit"] for row in payload["products"]}
    used |= {line["unit"] for order in payload["orders"] for line in order["items"]}
    assert used
    assert used <= set(DEFAULT_UNITS)


def test_product_ids_are_unique_within_the_section(tmp_path):
    payload = json.loads(_file(tmp_path).read_bytes().decode("utf-8"))
    for section in ("products", "customers", "orders"):
        ids = [row["id"] for row in payload[section]]
        assert len(ids) == len(set(ids)) == len(payload[section])


def test_lines_reference_products_of_the_same_file(tmp_path):
    payload = json.loads(_file(tmp_path).read_bytes().decode("utf-8"))
    product_ids = {row["id"] for row in payload["products"]}
    for order in payload["orders"]:
        assert order["items"]
        for line in order["items"]:
            assert line["product_id"] in product_ids


def test_order_dates_are_the_format_the_importer_accepts(tmp_path):
    payload = json.loads(_file(tmp_path).read_bytes().decode("utf-8"))
    for order in payload["orders"]:
        raw = order["date"]
        assert any(datetime.strptime(raw, fmt) for fmt in DATE_FORMATS)
        assert parse_order_date(raw).tzinfo is not None


def test_orders_vary_in_length_and_date(tmp_path):
    payload = json.loads(_file(tmp_path).read_bytes().decode("utf-8"))
    lengths = {len(order["items"]) for order in payload["orders"]}
    assert len(lengths) > 1
    days = {order["date"][:10] for order in payload["orders"]}
    assert len(days) > 30


def test_customers_get_inn_of_a_valid_length(tmp_path):
    payload = json.loads(_file(tmp_path).read_bytes().decode("utf-8"))
    for row in payload["customers"]:
        assert row["inn"].isdigit()
        assert len(row["inn"]) in (10, 12)
    assert len({row["name"] for row in payload["customers"]}) == len(payload["customers"])


def test_personal_names_have_gender_matching_given_name_and_patronymic():
    payload = build_payload(products=5, customers=200, orders=5, seed=SEED)
    male_given, male_patronymics = GIVEN[0]
    female_given, female_patronymics = GIVEN[1]
    male_surnames = {male for male, _ in SURNAMES}
    female_surnames = {female for _, female in SURNAMES}
    # Компании состоят из двух слов, а редкие дубли получают «№».
    personal = [
        row["name"] for row in payload["customers"]
        if len(row["name"].split()) == 3 and "№" not in row["name"]
    ]
    assert len(personal) >= 50
    for name in personal:
        surname, given, patronymic = name.split()
        if surname in female_surnames:
            assert given in female_given, name
            assert patronymic in female_patronymics, name
        else:
            assert surname in male_surnames, name
            assert given in male_given, name
            assert patronymic in male_patronymics, name


def test_product_adjective_agrees_with_the_noun(tmp_path):
    nouns = {noun: feminine for noun, feminine, _ in PRODUCTS}
    payload = json.loads(_file(tmp_path, products=200, customers=5, orders=5).read_bytes()
                         .decode("utf-8"))
    for row in payload["products"]:
        noun, adjective = row["name"].split(" ")[:2]
        assert adjective in (VARIANTS if not nouns[noun] else VARIANTS_FEMININE)


def test_names_are_unique_at_the_default_size(tmp_path):
    payload = json.loads(_file(tmp_path, products=DEFAULT_PRODUCTS, customers=DEFAULT_CUSTOMERS,
                              orders=5, seed=DEFAULT_SEED).read_bytes().decode("utf-8"))
    names = [row["name"] for row in payload["products"]]
    customers = [row["name"] for row in payload["customers"]]
    assert len(set(names)) == len(names) == DEFAULT_PRODUCTS
    assert len(set(customers)) == len(customers) == DEFAULT_CUSTOMERS


def test_the_raw_name_space_collides_at_the_default_size():
    rng = random.Random(DEFAULT_SEED)
    raw = []
    for _ in range(DEFAULT_PRODUCTS):
        noun, feminine, _units = PRODUCTS[rng.randrange(len(PRODUCTS))]
        variants = VARIANTS_FEMININE if feminine else VARIANTS
        raw.append(f"{noun} {variants[rng.randrange(len(variants))]} "
                   f"{SIZES[rng.randrange(len(SIZES))]}")
    assert len(set(raw)) < len(raw), "пробелов имён больше нет, гвард можно убрать"


def test_generated_file_passes_the_real_import(db_session, tmp_path):
    path = _file(tmp_path, products=40, customers=15, orders=60)
    preview = plan_import(db_session, tmp_path, [path.name])
    assert preview["unresolved_units"] == []
    report = run_import(db_session, tmp_path, [path.name])
    assert report["products"]["created"] == 40
    assert report["customers"]["created"] == 15
    assert report["orders"]["created"] == 60
    assert report["orders"]["empty_orders"] == 0
    assert report["warnings"] == []

    products = db_session.query(Product).all()
    assert len(products) == 40
    assert all(p.price > 0 for p in products)
    assert all(p.rest > 0 for p in products)
    assert len({p.name for p in products}) == 40
    assert {p.sku for p in products} == {f"1C-{i:05d}" for i in range(1, 41)}

    orders = db_session.query(Order).all()
    assert len(orders) == 60
    assert all(o.status == "done" for o in orders)
    assert all(o.total_amount > 0 for o in orders)
    assert max(len(o.items) for o in orders) > 1
    assert min(len(o.items) for o in orders) >= 1
    assert db_session.query(Customer).count() == 15


def test_a_new_order_goes_through_on_a_demo_product(db_session, tmp_path):
    path = _file(tmp_path, products=10, customers=5, orders=5)
    run_import(db_session, tmp_path, [path.name])
    product = db_session.query(Product).filter(Product.rest >= 3).first()
    customer = db_session.query(Customer).first()
    order = create_order(db_session, customer_id=customer.id,
                         items=[{"product_id": product.id, "quantity": 3, "discount": 0.0}])
    assert order.items[0].product_id == product.id
    assert order.items[0].product_price_snapshot == product.price
    assert order.total_amount > 0
    assert db_session.query(Order).filter(Order.id == order.id).one().items[0].quantity == 3


def test_the_demo_file_gives_the_forecast_page_real_numbers(db_session, tmp_path):
    """Прогноз на демо-данных обязан показывать числа, а не сплошные прочерки.

    Причина прежнего «везде мало данных» измерена на боевой базе, а не выведена из
    кода: 180 дней и 150 заказов на 300 товаров давали в среднем 1,5 дня с
    продажами товара при пороге 14, то есть дело в ПЛОТНОСТИ продаж, а не в длине
    ряда. Проверка идёт через настоящий импорт и настоящий прогноз — обе половины
    по отдельности прошли бы, а вместе ловят именно эту поломку.
    """
    path = _file(tmp_path, products=100, customers=20, orders=300, seed=5)
    run_import(db_session, tmp_path, [path.name])
    rows, total = forecast_rows(db_session, page_size=100)
    with_numbers = [row for row in rows if row["has_data"]]
    assert total == 100
    assert len(with_numbers) >= 10, (
        f"прогноз показал числа только у {len(with_numbers)} товаров из 100"
    )
    assert any(row["recommended_qty"] for row in with_numbers), (
        "нет ни одной рекомендации к закупке: фильтр «только к закупке» будет пуст"
    )
    assert any(row["forecast_7"] and row["forecast_30"] for row in with_numbers), (
        "горизонты прогноза пустые"
    )
    assert any(not row["has_data"] for row in rows), (
        "все товары с прогнозом: нечем показать состояние «мало данных»"
    )
    assert len(rows) == 100


def test_the_generator_writes_nothing_to_the_database(db_session, tmp_path):
    """Проверяет, что main() не пишет в базу.

    Ловушка на app.db.engine ставится автоусной фикстурой no_app_engine в
    conftest, а не здесь: она нужна каждому тесту этого файла, а не одному.
    """
    assert main(["--products", "30", "--customers", "10", "--orders", "40",
                 "--out", str(tmp_path / "demo.json"), "--seed", "3"]) == 0
    assert (tmp_path / "demo.json").is_file()
    assert db_session.query(Product).count() == 0
    assert db_session.query(Customer).count() == 0
    assert db_session.query(Order).count() == 0
    assert db_session.query(AuditLog).count() == 0


def test_the_generator_source_names_no_database():
    source = Path(gen_demo.__file__).read_text(encoding="utf-8")
    for token in ("SessionLocal", "create_engine", "get_db", "engine", "sessionmaker",
                  "from app.db", "app.db"):
        assert token not in source, f"в скрипте появился доступ к базе: {token}"


def test_command_line_writes_the_file_it_is_asked_for(tmp_path):
    out = tmp_path / "nested" / "demo.json"
    assert main(["--products", "5", "--customers", "3", "--orders", "7",
                 "--out", str(out), "--seed", "11"]) == 0
    payload = json.loads(out.read_bytes().decode("utf-8"))
    assert len(payload["products"]) == 5
    assert len(payload["customers"]) == 3
    assert len(payload["orders"]) == 7
    assert out.read_bytes() == write_payload(
        build_payload(5, 3, 7, 11), tmp_path / "again.json").read_bytes()
