import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FOLDER = ".system_folder/1c_export"
SECTIONS = ("products", "customers", "orders")
MAX_FILENAME_LENGTH = 255


class ImportFileError(ValueError):
    pass


def resolve_folder(folder: str | None) -> Path:
    raw = (folder or "").strip() or DEFAULT_FOLDER
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def list_payload_files(folder: str | Path) -> list[dict]:
    root = Path(folder)
    if not root.is_dir():
        return []
    return [
        {"name": p.name, "size": p.stat().st_size, "modified": p.stat().st_mtime}
        for p in sorted(root.glob("*.json"))
        if p.is_file()
    ]


def _checked_path(folder: Path, name: str) -> Path:
    if not name or len(name) > MAX_FILENAME_LENGTH or name in (".", ".."):
        raise ImportFileError("некорректное имя файла")
    if "/" in name or "\\" in name or Path(name).name != name:
        raise ImportFileError("имя файла не должно содержать путь")
    path = folder / name
    if not path.is_file():
        raise ImportFileError(f"файл не найден: {name}")
    return path


def read_payloads(folder: str | Path, filenames: list[str]) -> dict[str, list[dict]]:
    if not filenames:
        raise ImportFileError("не выбрано ни одного файла")
    root = Path(folder)
    sections: dict[str, list[dict]] = {name: [] for name in SECTIONS}
    for name in filenames:
        path = _checked_path(root, name)
        try:
            payload = json.loads(path.read_bytes().decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ImportFileError(f"{name}: не удалось прочитать JSON ({err})") from err
        if not isinstance(payload, dict):
            raise ImportFileError(f"{name}: ожидался объект с разделами")
        for section, rows in payload.items():
            if section not in sections:
                continue
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ImportFileError(f"{name}: раздел {section} должен быть списком объектов")
            sections[section].extend(rows)
    return sections
