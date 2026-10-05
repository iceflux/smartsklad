import shutil
import subprocess
from pathlib import Path

import pytest

from app.modules.settings.service import DEFAULT_SEARCH_SLOW_MS

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "app" / "templates" / "base.html"
HARNESS = HERE / "search_modal_script.js"


def test_the_search_window_debounces_cancels_and_closes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен")
    r = subprocess.run(
        [node, str(HARNESS), str(TEMPLATE), str(DEFAULT_SEARCH_SLOW_MS)],
        capture_output=True,
    )
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert r.returncode == 0, out
    assert out.strip().endswith("ok"), (
        "харнесс закончился без отметки: молчаливый выход с кодом 0 уже проходил "
        "здесь как успех, и все проверки под ним остались невыполненными. "
        f"Вывод был: {out!r}"
    )