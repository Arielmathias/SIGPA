"""Identificación de clientes por teléfono.

El teléfono puede venir (desde WhatsApp) o estar guardado (en la tabla
cliente, cargado a mano o desde el panel) en distintos formatos: con o sin
"+", con o sin prefijo de país 56, con espacios o guiones. Todas las
búsquedas por teléfono comparan solo dígitos y aceptan ambas variantes del
prefijo 56, para que un mismo cliente se reconozca sin importar el formato.
"""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Cliente

PREFIJO_CHILE = "56"


def normalizar_telefono(telefono: str | None) -> str:
    """Devuelve solo dígitos y con prefijo 56 (ej. "+56 9 5772 1243",
    "56957721243" y "957721243" quedan todos como "56957721243")."""
    digitos = re.sub(r"\D", "", telefono or "")
    if len(digitos) == 9 and digitos.startswith("9"):
        return PREFIJO_CHILE + digitos
    return digitos


def _variantes_telefono(telefono: str | None) -> list[str]:
    normalizado = normalizar_telefono(telefono)
    if not normalizado:
        return []
    variantes = [normalizado]
    if normalizado.startswith(PREFIJO_CHILE) and len(normalizado) > len(PREFIJO_CHILE):
        variantes.append(normalizado[len(PREFIJO_CHILE):])
    return variantes


async def buscar_clientes_por_telefono(session: AsyncSession, telefono: str | None) -> list[Cliente]:
    """Todos los clientes cuyo teléfono coincide con el dado una vez
    normalizado. Devuelve una lista (no un único cliente) a propósito: si hay
    más de uno, quien llama decide qué hacer (ver escalamiento a la ejecutiva
    en order_flow.py) en vez de elegir uno arbitrariamente."""
    variantes = _variantes_telefono(telefono)
    if not variantes:
        return []
    telefono_solo_digitos = func.regexp_replace(Cliente.telefono, r"\D", "", "g")
    result = await session.execute(
        select(Cliente).where(telefono_solo_digitos.in_(variantes)).order_by(Cliente.id)
    )
    return list(result.scalars().all())
