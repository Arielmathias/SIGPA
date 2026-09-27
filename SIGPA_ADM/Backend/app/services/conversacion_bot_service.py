"""Estado de "conversación activa" por teléfono: implementa la restricción de
que el agente solo procesa conversaciones que él mismo inició (ver
marcar_activa/marcar_inactiva y su uso en whatsapp.py y order_flow.py)."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal
from app.models.conversacion_bot import ConversacionBot


async def marcar_activa(phone: str) -> None:
    """Deja la conversación de este teléfono como activa. La usará el futuro
    cron del recordatorio (aún no existe) para "abrir" la conversación antes
    de que el cliente escriba."""
    ahora = datetime.utcnow()
    stmt = pg_insert(ConversacionBot).values(
        telefono=phone,
        activa=True,
        iniciada_en=ahora,
        actualizada_en=ahora,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ConversacionBot.telefono],
        set_={"activa": True, "iniciada_en": ahora, "actualizada_en": ahora},
    )
    async with SessionLocal() as session:
        await session.execute(stmt)
        await session.commit()


async def marcar_inactiva(phone: str) -> None:
    """Cierra la conversación de este teléfono. Funciona aunque no exista
    fila previa (upsert)."""
    ahora = datetime.utcnow()
    stmt = pg_insert(ConversacionBot).values(
        telefono=phone,
        activa=False,
        actualizada_en=ahora,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ConversacionBot.telefono],
        set_={"activa": False, "actualizada_en": ahora},
    )
    async with SessionLocal() as session:
        await session.execute(stmt)
        await session.commit()


async def esta_activa(phone: str) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ConversacionBot.activa).where(ConversacionBot.telefono == phone)
        )
        activa = result.scalar_one_or_none()
    return bool(activa)
