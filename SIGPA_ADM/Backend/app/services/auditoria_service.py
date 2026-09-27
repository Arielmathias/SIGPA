"""Registro de auditoría de cambios administrativos (crear/actualizar) sobre
producto/cliente/pedido. Resiliente: nunca debe romper el endpoint que la
invoca (ver registrar_auditoria)."""

import logging
from datetime import date, datetime
from decimal import Decimal

from app.core.database import SessionLocal
from app.models.auditoria import Auditoria

logger = logging.getLogger(__name__)


def _valor_serializable(valor):
    # JSONB no acepta datetime/date ni Decimal (columnas Numeric) tal cual:
    # json.dumps fallaría al serializarlos.
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    return valor


def construir_snapshot(obj) -> dict:
    """Convierte un objeto ORM en un dict plano (columnas de su tabla) apto
    para guardar en antes/despues de la auditoría."""
    return {
        columna.name: _valor_serializable(getattr(obj, columna.name))
        for columna in obj.__table__.columns
    }


async def registrar_auditoria(
    usuario: str,
    entidad: str,
    entidad_id: int,
    accion: str,
    antes: dict | None = None,
    despues: dict | None = None,
) -> None:
    try:
        async with SessionLocal() as session:
            session.add(
                Auditoria(
                    usuario=usuario,
                    entidad=entidad,
                    entidad_id=entidad_id,
                    accion=accion,
                    antes=antes,
                    despues=despues,
                )
            )
            await session.commit()
    except Exception:
        logger.exception(
            "[auditoria] Falló registrar_auditoria (usuario=%s, entidad=%s, entidad_id=%s, accion=%s)",
            usuario,
            entidad,
            entidad_id,
            accion,
        )
