import logging

import httpx

from app.core.config import settings
from app.services.mensaje_whatsapp_service import registrar_mensaje

logger = logging.getLogger(__name__)


async def send_whatsapp_message(to: str, message: str) -> dict | None:
    url = f"{settings.META_API_URL}/{settings.META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.META_WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)

    try:
        response_body = response.json()
    except ValueError:
        response_body = response.text

    # TEMPORAL: log completo para debugging, quitar cuando se resuelva el problema.
    logger.info(
        "WhatsApp API response (status %s): %s",
        response.status_code,
        response_body,
    )

    exitoso = response.status_code == 200

    meta_message_id = None
    if exitoso and isinstance(response_body, dict):
        mensajes_respuesta = response_body.get("messages") or []
        if mensajes_respuesta:
            meta_message_id = mensajes_respuesta[0].get("id")

    await registrar_mensaje(
        telefono=to,
        direccion="saliente",
        tipo="text",
        contenido=message,
        meta_message_id=meta_message_id,
        estado="enviado" if exitoso else "fallido",
    )

    if not exitoso:
        return None

    return response_body
