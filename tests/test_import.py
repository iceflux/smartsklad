from app.modules.auth.permissions import Permission
from app.modules.auth.roles_router import PERMISSION_LABELS


def test_import_permission_exists():
    assert Permission.import_manage.value == "import.manage"


def test_import_permission_has_label():
    assert PERMISSION_LABELS["import.manage"] == "Импорт данных из 1С"
