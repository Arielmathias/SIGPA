from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class MensajeWhatsApp(Base):
    __tablename__ = "mensaje_whatsapp"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"), nullable=True)
    pedido_id: Mapped[int | None] = mapped_column(ForeignKey("pedido.id"), nullable=True)
    telefono: Mapped[str] = mapped_column(String(30), nullable=False)
    meta_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    direccion: Mapped[str] = mapped_column(
        SAEnum("entrante", "saliente", name="direccion_mensaje"),
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    contenido: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[str | None] = mapped_column(String(50), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
