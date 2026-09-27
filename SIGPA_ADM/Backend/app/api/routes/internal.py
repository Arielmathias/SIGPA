import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.cliente import Cliente
from app.models.conversacion_bot import ConversacionBot
from app.models.enums import DiaSemana
from app.services.conversacion_bot_service import marcar_activa
from app.services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

ZONA_HORARIA_CHILE = ZoneInfo("America/Santiago")

# Índice = date.weekday() (0=Lunes...6=Domingo). Los dos últimos no existen
# como valor del enum DiaSemana (solo hay reparto Lunes-Viernes), se usan
# solo para mostrar el nombre del día en la respuesta.
_NOMBRES_DIA_SEMANA = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]


def _dia_semana_manana() -> tuple[str, DiaSemana | None]:
    hoy_chile = datetime.now(ZONA_HORARIA_CHILE).date()
    manana = hoy_chile + timedelta(days=1)
    nombre = _NOMBRES_DIA_SEMANA[manana.weekday()]
    try:
        return nombre, DiaSemana(nombre)
    except ValueError:
        return nombre, None


def _fecha_chile(fecha: datetime) -> date:
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=ZoneInfo("UTC"))
    return fecha.astimezone(ZONA_HORARIA_CHILE).date()


@router.post("/recordatorio-diario")
async def recordatorio_diario(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    if not settings.INTERNAL_CRON_SECRET or x_cron_secret != settings.INTERNAL_CRON_SECRET:
        raise HTTPException(status_code=401, detail="No autorizado")

    ahora_chile = datetime.now(ZONA_HORARIA_CHILE)
    if ahora_chile.hour != 15:
        # Ventana horaria fija en hora LOCAL de Chile: al comparar contra
        # zoneinfo (no contra UTC) el chequeo es inmune a los cambios de
        # horario de verano, sin ajuste manual del cron. Además de la
        # idempotencia por fecha (ver telefonos_ya_enviados_hoy más abajo),
        # esto permite invocar el endpoint varias veces por hora sin
        # duplicar envíos.
        return {
            "status": "skipped",
            "razon": "fuera de la ventana horaria",
            "hora_actual_chile": ahora_chile.isoformat(),
        }

    nombre_dia_manana, dia_enum_manana = _dia_semana_manana()
    hoy_chile = datetime.now(ZONA_HORARIA_CHILE).date()

    clientes: list[Cliente] = []
    if dia_enum_manana is not None:
        async with SessionLocal() as session:
            result = await session.execute(
                select(Cliente).where(
                    Cliente.dia_reparto == dia_enum_manana,
                    Cliente.activo.is_(True),
                    Cliente.opt_out_whatsapp.is_(False),
                )
            )
            clientes = list(result.scalars().all())

    telefonos_enviados: list[str] = []
    telefonos_ya_enviados_hoy: list[str] = []
    errores: list[dict] = []

    for cliente in clientes:
        telefono = cliente.telefono
        if not telefono:
            errores.append({"cliente_id": cliente.id, "error": "Cliente sin teléfono registrado"})
            continue

        try:
            async with SessionLocal() as session:
                result = await session.execute(
                    select(ConversacionBot).where(ConversacionBot.telefono == telefono)
                )
                conversacion = result.scalar_one_or_none()

            if (
                conversacion is not None
                and conversacion.iniciada_en is not None
                and _fecha_chile(conversacion.iniciada_en) == hoy_chile
            ):
                telefonos_ya_enviados_hoy.append(telefono)
                continue

            mensaje = (
                f"¡Hola {cliente.nombre}! Te saludamos de Comercial De María — "
                "mañana repartimos en tu sector. ¿Deseas realizar tu pedido?"
            )
            # TODO: reemplazar por plantilla aprobada de Meta cuando esté lista.
            await send_whatsapp_message(to=telefono, message=mensaje)
            await marcar_activa(telefono)
            telefonos_enviados.append(telefono)
        except Exception as exc:
            logger.exception("[recordatorio-diario] Falló el envío para %s", telefono)
            errores.append({"telefono": telefono, "error": str(exc)})

    return {
        "clientes_evaluados": len(clientes),
        "mensajes_enviados": len(telefonos_enviados),
        "ya_enviados_hoy": len(telefonos_ya_enviados_hoy),
        "dia_manana": nombre_dia_manana,
        "telefonos_enviados": telefonos_enviados,
        "telefonos_ya_enviados_hoy": telefonos_ya_enviados_hoy,
        "errores": errores,
    }
