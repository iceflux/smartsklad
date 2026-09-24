from pathlib import Path

from sqlalchemy.orm import Session

from app.modules.backup.models import BackupSettings

DEFAULT_BACKUP_DIR = Path(__file__).resolve().parents[3] / "backups"


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
