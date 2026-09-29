import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.modules.auth import ratelimit
from app.modules.auth.models import User
from app.modules.auth.service import get_role_by_name, hash_password

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / "app" / "templates"
HARNESS = HERE / "login_page_script.js"


def _node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен")
    return node


def _run_harness(node, template, form_id, error_id, fallback, success_redirect, cases, tmp_path):
    path = tmp_path / ("%s.json" % template.stem)
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [node, str(HARNESS), str(template), form_id, error_id, fallback, success_redirect, str(path)],
        capture_output=True,
    )
    assert r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")


def _real_429_detail(client, db_session):
    _seller(db_session)
    for _ in range(ratelimit.MAX_FAILURES):
        assert client.post(
            "/api/auth/login", json={"username": "seller1", "password": "wrong"}
        ).status_code == 401
    r = client.post("/api/auth/login", json={"username": "seller1", "password": "wrong"})
    assert r.status_code == 429
    return r.json()["detail"]


def _seller(db_session, username="seller1"):
    u = User(username=username, password_hash=hash_password("password123"),
             role_id=get_role_by_name(db_session, "seller").id)
    db_session.add(u)
    db_session.commit()
    return u


def test_login_page_shows_the_throttle_text_the_server_sent(client, db_session, tmp_path):
    node = _node()
    detail = _real_429_detail(client, db_session)
    assert "Слишком много попыток входа" in detail
    _run_harness(
        node,
        TEMPLATES / "login.html",
        "login-form",
        "error",
        "Неверный логин или пароль",
        "/",
        {
            "throttled": detail,
            "refusedStatus": 401,
            "refused": "Неверный логин или пароль",
        },
        tmp_path,
    )


def test_reset_password_page_shows_the_text_the_server_sent(tmp_path):
    node = _node()
    _run_harness(
        node,
        TEMPLATES / "reset_password.html",
        "reset-form",
        "error",
        "Ошибка. Попробуйте ещё раз.",
        "/login",
        {
            "throttled": "Слишком много запросов на восстановление пароля. Повторите через 12 с.",
            "refusedStatus": 400,
            "refused": "Неверная или устаревшая ссылка",
        },
        tmp_path,
    )
