from app.modules.auth.models import Permission


def parse_permissions(raw: list[str]) -> list[str]:
    valid = {p.value for p in Permission}
    bad = set(raw) - valid
    if bad:
        raise ValueError(f"unknown permissions: {sorted(bad)}")
    return sorted(set(raw))