import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "app" / "templates" / "base.html"
HARNESS = HERE / "list_live_filter_script.js"


def test_the_search_box_filters_only_after_a_pause():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен")
    r = subprocess.run([node, str(HARNESS), str(TEMPLATE)], capture_output=True)
    assert r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")
