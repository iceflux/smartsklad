from datetime import datetime, time as dtime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Time, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BackupSettings(Base):
    __tablename__ = "backup_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_time: Mapped[dtime] = mapped_column(Time, default=dtime(3, 0))
    check_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    local_path: Mapped[str] = mapped_column(String(512), default="")
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    pg_dump_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_report_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_report_skipped: Mapped[int] = mapped_column(Integer, default=0)


class BackupLog(Base):
    __tablename__ = "backup_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
