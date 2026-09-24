import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.modules.audit.service import log_action
from app.modules.backup.models import BackupLog, BackupSettings

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
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
        "PGDATABASE": (url.database or "").lstrip("/"),
    }
    host = url.host or url.query.get("host")
    if host:
        env["PGHOST"] = host
    if url.password:
        env["PGPASSWORD"] = url.password
    return env


def pg_dump_command(binary: str, target: Path) -> list[str]:
    return [binary, "--format=custom", "--no-password", "--file", str(target)]


def pg_restore_command(binary: str, archive: Path) -> dict[str, list[str] | dict[str, str]]:
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
    if target.exists():
        return target.is_dir() and os.access(target, os.W_OK)
    parent = target.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def _dump_major(dump: str) -> int | None:
    try:
        result = subprocess.run([dump, "--version"], capture_output=True, text=True, timeout=10)
        return parse_binary_version(result.stdout or "")
    except Exception:
        return None


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


def archive_name(now: datetime) -> str:
    return f"smartsklad-{now.strftime('%Y%m%d-%H%M%S')}.dump"


def parse_archive_name(name: str) -> datetime | None:
    match = re.fullmatch(r"smartsklad-(\d{8})-(\d{6})(?:-\d+)?\.dump", name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def list_archives(bs) -> list[dict]:
    directory = Path(bs.local_path)
    if not directory.is_dir():
        return []
    archives = []
    for path in directory.iterdir():
        if not path.is_file() or parse_archive_name(path.name) is None:
            continue
        archives.append({"name": path.name, "size_bytes": path.stat().st_size, "path": str(path)})
    archives.sort(key=lambda a: a["name"], reverse=True)
    return archives


def delete_expired(bs, now: datetime) -> int:
    limit = now - timedelta(days=bs.retention_days)
    removed = 0
    for path in Path(bs.local_path).iterdir() if Path(bs.local_path).is_dir() else []:
        stamp = parse_archive_name(path.name)
        if stamp is not None and path.is_file() and stamp < limit:
            path.unlink()
            removed += 1
    return removed


def _record(db, *, kind, status, filename=None, size_bytes=None, error=None, user_id=None) -> dict:
    entry = BackupLog(kind=kind, status=status, filename=filename,
                      size_bytes=size_bytes, error=error, user_id=user_id)
    db.add(entry)
    db.commit()
    log_action(db, action="backup.run", entity_type="backup", entity_id=entry.id,
               new_value=status, user_id=user_id)
    return {"kind": kind, "status": status, "filename": filename,
            "size_bytes": size_bytes, "error": error, "log_id": entry.id}


def create_backup(db, *, user_id: int | None = None, kind: str = "manual",
                  now: datetime | None = None) -> dict:
    if kind not in ("scheduled", "manual", "restore"):
        raise ValueError("unknown backup kind")
    now = now or datetime.now()
    bs = get_backup_settings(db)
    directory = Path(bs.local_path)
    partial = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        binary = find_binary("pg_dump", bs.pg_dump_path)
        if binary is None:
            return _record(db, kind=kind, status="failed",
                           error="pg_dump не найден", user_id=user_id)
        target = unique_path(directory, f"smartsklad-{now.strftime('%Y%m%d-%H%M%S')}", ".dump")
        partial = directory / (target.name + ".part")
        result = subprocess.run(pg_dump_command(binary, partial),
                                env={**os.environ, **pg_env()},
                                capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            return _record(db, kind=kind, status="failed",
                           error=(result.stderr or "pg_dump завершился с ошибкой").strip()[:512],
                           user_id=user_id)
        partial.replace(target)
        partial = None
        size = target.stat().st_size
        delete_expired(bs, now)
        bs.last_backup_at = now
        bs.last_status = "success"
        bs.last_error = None
        db.commit()
        return _record(db, kind=kind, status="success", filename=target.name,
                       size_bytes=size, user_id=user_id)
    except Exception as error:
        db.rollback()
        if partial is not None:
            partial.unlink(missing_ok=True)
        return _record(db, kind=kind, status="failed", error=str(error)[:512], user_id=user_id)
