import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.modules.inventory.service import _plural

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "app" / "templates" / "categories.html"
HARNESS = HERE / "categories_delete_script.js"
PLURAL_NUMBERS = [0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 31, 101, 111, 112, 121]


def test_the_delete_button_builds_the_delete_url_from_the_offered_names(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен")
    payload = {
        "tree": [
            {"id": 1, "name": "Корень", "parent_id": None, "children": [{"id": 2, "name": "Ветка"}]},
            {"id": 3, "name": "Другое", "parent_id": None, "children": [{"id": 4, "name": "Лист"}]},
        ],
        "tree_alone": [{"id": 9, "name": "Единственная", "parent_id": None, "children": []}],
        "plural": [[n, _plural(n, "товар", "товара", "товаров")] for n in PLURAL_NUMBERS],
    }
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [node, str(HARNESS), str(TEMPLATE), str(cases)],
        capture_output=True,
    )
    assert r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")
