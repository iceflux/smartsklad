from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ENTITY_PRODUCT = "product"
ENTITY_CUSTOMER = "customer"
ENTITY_ORDER = "order"


class ImportMap(Base):
    __tablename__ = "import_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int] = mapped_column(Integer)
    source_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("entity_type", "source_id", name="uq_import_map"),)
