import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.modules.backup.models import BackupSettings

DEFAULT_BACKUP_DIR = Path(__file__).resolve().parents[3] / "backups"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ensure_default_backup_settings(db: Session) -> BackupSettings:
    row = db.query(BackupSettings).first()
    if row is not None:
        return row
    row = BackupSettings(local_path=str(DEFAULT_BACKUP_DIR))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_backup_settings(db: Session) -> BackupSettings:
    return ensure_default_backup_settings(db)


def find_binary(name: str, configured: str | None = None) -> str | None:
    if configured:
        candidate = Path(configured)
        return str(candidate) if candidate.is_file() else None
    return shutil.which(name)


def pg_env() -> dict[str, str]:
    url = make_url(app_settings.database_url)
    env = {
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
        "PGDATABASE": (url.database or "").lstrip("/"),
    }
    if url.password:
        env["PGPASSWORD"] = url.password
    return env


def pg_dump_command(binary: str, target: Path) -> list[str]:
    return [binary, "--format=custom", "--no-password", "--file", str(target)]


def pg_restore_command(binary: str, archive: Path) -> dict[str, list[str]]:
    return {
        "command": [binary, "--clean", "--if-exists", "--no-password",
                    "--dbname", pg_env()["PGDATABASE"], str(archive)],
        "env": pg_env(),
    }


def parse_binary_version(text: str) -> int | None:
    match = re.search(r"(\d+)\.\d+", text)
    return int(match.group(1)) if match else None


def is_compatible(dump_major: int | None, server_major: int | None) -> bool | None:
    if dump_major is None or server_major is None:
        return None
    return dump_major >= server_major


def _server_major(db: Session) -> int | None:
    try:
        value = db.execute(text("SHOW server_version")).scalar()
    except Exception:
        return None
    return parse_binary_version(str(value)) if value else None


def _path_writable(path: str) -> bool:
    target = Path(path)
    probe = target if target.is_dir() else target.parent
    return probe.is_dir() and os.access(probe, os.W_OK)


def _dump_major(dump: str) -> int | None:
    try:
        result = subprocess.run([dump, "--version"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return parse_binary_version(result.stdout or "")


def pg_dump_health(db: Session, bs: BackupSettings) -> dict:
    dump = find_binary("pg_dump", bs.pg_dump_path)
    restore = None
    if dump and bs.pg_dump_path:
        sibling = Path(dump).with_name("pg_restore" + Path(dump).suffix)
        restore = str(sibling) if sibling.is_file() else None
    if restore is None and not bs.pg_dump_path:
        restore = shutil.which("pg_restore")
    dump_major = _dump_major(dump) if dump else None
    server_major = _server_major(db)
    return {
        "dump_found": dump is not None,
        "dump_path": dump,
        "restore_found": restore is not None,
        "restore_path": restore,
        "dump_major": dump_major,
        "server_major": server_major,
        "compatible": is_compatible(dump_major, server_major),
        "path_writable": _path_writable(bs.local_path),
    }
