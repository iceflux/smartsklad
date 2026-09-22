from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str] = mapped_column(String(256), default="")
    subject: Mapped[str | None] = mapped_column(String(256), nullable=True)
    text_body: Mapped[str] = mapped_column(String(4096), default="")
    document_type: Mapped[str] = mapped_column(String(16), default="order")
    document_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="sent")
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())