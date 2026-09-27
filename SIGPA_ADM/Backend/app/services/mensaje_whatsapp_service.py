"""Trazabilidad cruda de mensajes de WhatsApp (entrantes y salientes), para el
futuro panel. No debe romper el envío/recepción real si falla (ver
registrar_mensaje)."""

import logging

from app.core.database import SessionLocal
from app.models.mensaje_whatsapp import MensajeWhatsApp

logger = logging.getLogger(__name__)


async def registrar_mensaje(
    telefono: str,
    direccion: str,
    tipo: str,
    contenido: str | None = None,
    cliente_id: int | None = None,
    pedido_id: int | None = None,
    meta_message_id: str | None = None,
    estado: str | None = None,
) -> None:
    try:
        async with SessionLocal() as session:
            session.add(
                MensajeWhatsApp(
                    cliente_id=cliente_id,
                    pedido_id=pedido_id,
                    telefono=telefono,
                    meta_message_id=meta_message_id,
                    direccion=direccion,
                    tipo=tipo,
                    contenido=contenido,
                    estado=estado,
                )
            )
            await session.commit()
    except Exception:
        logger.exception(
            "[mensaje_whatsapp] Falló registrar_mensaje (telefono=%s, direccion=%s, tipo=%s)",
            telefono,
            direccion,
            tipo,
        )
