import app.modules.inventory.models  # noqa: F401
from app.db import Base


def test_inventory_tables_registered():
    for t in ("categories", "products", "price_history"):
        assert t in Base.metadata.tables