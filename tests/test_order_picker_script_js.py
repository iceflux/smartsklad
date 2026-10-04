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
    r = subprocess.run([node, str(HARNESS), str(TEMPLATE)], capture_output=True)
    assert r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")
