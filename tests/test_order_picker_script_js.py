"""Клиентская половина подстановки и окон: скрипт страницы заказа на выдуманном
DOM, без сети.

Скрипт гоняется ОТРЕНДЕРЕННЫМ: раньше прибор читал исходник шаблона и подставлял
подстановки Jinja сам, и это стоило двух подпорок — словаря значений из
приложения и целой грамматики `{% if %}` в приборе. Страница и так рендерится
TestClient'ом, поэтому честнее отдать прибору ровно тот текст, который получит
браузер, и проверять, что в нём не осталось ни одной подстановки.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "app" / "templates" / "order_form.html"
HARNESS = HERE / "order_picker_script.js"
PASSWORD = "password123"


def _admin(client, db_session):
    from app.modules.auth.models import User
    from app.modules.auth.service import get_role_by_name, hash_password, login

    db_session.add(User(username="boss", password_hash=hash_password(PASSWORD),
                        role_id=get_role_by_name(db_session, "admin").id))
    db_session.commit()
    client.cookies.set("session", login(db_session, "boss", PASSWORD).token)


def _script_of(page: str) -> str:
    """Скрипты САМОЙ страницы заказа, без скриптов `base.html`.

    Скрипты шапки и подвала (меню, живая фильтрация) к подстановке отношения не
    имеют, а подменять их прибору пришлось бы лишней обвязкой. Отбираются
    сравнением с самим `base.html` — по тексту блока, а не по позиции, иначе
    прибор зависел бы от того, где именно страница положила свой скрипт."""
    base = (TEMPLATE.parent / "base.html").read_text(encoding="utf-8")
    from_base = {block.strip() for block in
                 re.findall(r"<script>([\s\S]*?)</script>", base)}
    blocks = [block for block in re.findall(r"<script>([\s\S]*?)</script>", page)
              if block.strip() not in from_base]
    assert blocks, "на странице заказа не нашлось ни одного своего блока <script>"
    source = "\n".join(blocks)
    assert "data-picker-form" in source, (
        "выбран не тот скрипт: на странице нет подстановки")
    assert "{%" not in source and "{{" not in source, (
        "в отрендеренном скрипте осталась подстановка Jinja — прибор взял бы не тот "
        "код, который работает в браузере")
    return source


def test_the_picker_filters_by_typing_and_survives_the_keyboard(client, db_session, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен")
    _admin(client, db_session)
    page = client.get("/orders/new").text
    rendered = tmp_path / "order_script.js"
    rendered.write_text(_script_of(page), encoding="utf-8")
    r = subprocess.run([node, str(HARNESS), str(rendered)], capture_output=True)
    assert r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")