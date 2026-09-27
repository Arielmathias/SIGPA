from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class ConversacionBot(Base):
    __tablename__ = "conversacion_bot"

    telefono: Mapped[str] = mapped_column(String(30), primary_key=True)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    iniciada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
