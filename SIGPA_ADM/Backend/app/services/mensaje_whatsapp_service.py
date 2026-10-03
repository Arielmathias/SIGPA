"""Trazabilidad cruda de mensajes de WhatsApp (entrantes y salientes), para el
futuro panel. No debe romper el envío/recepción real si falla (ver
registrar_mensaje)."""

import logging

from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.mensaje_whatsapp import MensajeWhatsApp

logger = logging.getLogger(__name__)

# Índice único de los wamid entrantes: violarlo significa "mensaje duplicado".
INDICE_WAMID_ENTRANTE = "uq_mensaje_whatsapp_entrante_meta_message_id"


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


async def registrar_mensaje_entrante(
    telefono: str,
    tipo: str,
    contenido: str | None,
    meta_message_id: str | None,
) -> bool:
    """Registra un mensaje entrante y dice si hay que procesarlo: False si
    ese wamid ya estaba registrado (Meta entregó el mismo mensaje más de una
    vez), True en cualquier otro caso.

    Quien decide es el índice único parcial sobre meta_message_id de los
    entrantes (migración 20261003202757, ver MensajeWhatsApp.__table_args__):
    si el insert lo viola, es un duplicado. Cubre los reintentos que llegan
    después de un reinicio (cuando el registro en memoria de webhook_queue ya
    se perdió) y la carrera entre dos procesos, sin consultar antes. Si la BD
    falla por cualquier otro motivo se procesa igual: perder la trazabilidad
    es mejor que no responder.
    """
    try:
        async with SessionLocal() as session:
            session.add(
                MensajeWhatsApp(
                    telefono=telefono,
                    meta_message_id=meta_message_id,
                    direccion="entrante",
                    tipo=tipo,
                    contenido=contenido,
                    estado="recibido",
                )
            )
            await session.commit()
    except IntegrityError as exc:
        if INDICE_WAMID_ENTRANTE in str(exc.orig):
            logger.info("[mensaje_whatsapp] Mensaje entrante duplicado (wamid=%s)", meta_message_id)
            return False
        logger.exception(
            "[mensaje_whatsapp] Falló registrar_mensaje_entrante (telefono=%s, wamid=%s)",
            telefono,
            meta_message_id,
        )
    except Exception:
        logger.exception(
            "[mensaje_whatsapp] Falló registrar_mensaje_entrante (telefono=%s, wamid=%s)",
            telefono,
            meta_message_id,
        )
    return True
