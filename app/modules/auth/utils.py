from app.modules.auth.models import Permission

ROLE_NAMES = {"admin", "seller", "warehouseman", "manager"}


def parse_permissions(raw: list[str]) -> list[str]:
    valid = {p.value for p in Permission}
    bad = set(raw) - valid
    if bad:
        raise ValueError(f"unknown permissions: {sorted(bad)}")
    return sorted(set(raw))


def is_system_role_name(name: str) -> bool:
    return name in ROLE_NAMES