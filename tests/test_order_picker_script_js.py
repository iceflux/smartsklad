"""Клиентская половина подстановки и окон: скрипт страницы заказа на выдуманном
DOM, без сети.

Значения подстановок Jinja передаются из приложения, а не зашиты в прибор: иначе
прибор проверял бы свою копию числа, а не то, что реально попадёт в страницу, и
расхождение порога заметил бы только владелец — молча, отказом на первой букве.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "app" / "templates" / "order_form.html"
HARNESS = HERE / "order_picker_script.js"


def test_the_picker_filters_by_typing_and_survives_the_keyboard():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен")
    from app.main import LOOKUP_MIN_QUERY

    values = json.dumps({"LOOKUP_MIN_QUERY": LOOKUP_MIN_QUERY})
    r = subprocess.run([node, str(HARNESS), str(TEMPLATE), values],
                       capture_output=True)
    assert r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")
