"""Estado de "conversación activa" por teléfono: implementa la restricción de
que el agente solo procesa conversaciones que él mismo inició (ver
marcar_activa/marcar_inactiva y su uso en whatsapp.py y order_flow.py)."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal
from app.models.conversacion_bot import ConversacionBot

ZONA_HORARIA_CHILE = ZoneInfo("America/Santiago")

# Una conversación activa expira automáticamente a las 07:00 hora Chile del
# día siguiente a iniciada_en (ver esta_activa), para no dejarla abierta
# indefinidamente si el cliente nunca la cierra confirmando un pedido.
HORA_CORTE_CONVERSACION = 7


def _a_hora_chile(fecha: datetime) -> datetime:
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=ZoneInfo("UTC"))
    return fecha.astimezone(ZONA_HORARIA_CHILE)


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
            select(ConversacionBot).where(ConversacionBot.telefono == phone)
        )
        conversacion = result.scalar_one_or_none()

    if conversacion is None or not conversacion.activa:
        return False

    if conversacion.iniciada_en is None:
        # No debería ocurrir (marcar_activa siempre setea iniciada_en), pero
        # sin ese dato no hay corte que calcular: se mantiene activa.
        return True

    iniciada_en_chile = _a_hora_chile(conversacion.iniciada_en)
    corte = datetime.combine(
        iniciada_en_chile.date() + timedelta(days=1),
        time(hour=HORA_CORTE_CONVERSACION),
        tzinfo=ZONA_HORARIA_CHILE,
    )

    if datetime.now(ZONA_HORARIA_CHILE) >= corte:
        await marcar_inactiva(phone)
        return False

    return True
