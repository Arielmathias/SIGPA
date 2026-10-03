import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.core.config import (
    MENSAJE_NOTIFICACION_EJECUTIVA,
    MENSAJE_SALUDO_ESPONTANEO,
    settings,
)
from app.services.conversacion_bot_service import esta_activa
from app.services.mensaje_whatsapp_service import registrar_mensaje_entrante
from app.services.order_flow import procesar_mensaje
from app.services.webhook_queue import encolar, reservar_wamid
from app.services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

MENSAJE_ERROR_GENERICO = (
    "Ocurrió un problema procesando tu mensaje, por favor intenta de nuevo o contacta a un ejecutivo."
)

MENSAJE_TIPO_NO_SOPORTADO = "Por ahora solo puedo procesar mensajes de texto o de ubicación."


async def _enrutar_a_ejecutiva(phone_number: str) -> None:
    """Restricción "el agente solo procesa conversaciones que él inicia":
    cuando no hay conversación activa (esta_activa == False), en vez de
    procesar el mensaje con el bot se saluda al cliente y se avisa a la
    ejecutiva para que continúe manualmente."""
    logger.info(
        "[WhatsApp] Mensaje de %s enrutado a la ejecutiva: no hay conversación activa del bot",
        phone_number,
    )
    await send_whatsapp_message(to=phone_number, message=MENSAJE_SALUDO_ESPONTANEO)
    await send_whatsapp_message(
        to=settings.EJECUTIVA_PHONE,
        message=MENSAJE_NOTIFICACION_EJECUTIVA.format(telefono=phone_number),
    )


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.META_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge, status_code=200)
    return PlainTextResponse(content="Forbidden", status_code=403)


async def _procesar_mensaje_entrante(message: dict) -> None:
    """Procesa un mensaje entrante ya aceptado por el webhook (corre en la
    cola del teléfono, ver webhook_queue). Ninguna excepción sale de aquí
    sin loguearse: Meta ya recibió su 200."""
    phone_number = message["from"]
    message_type = message.get("type")
    wamid = message.get("id")

    if message_type == "text":
        message_text = message.get("text", {}).get("body")
        print(f"[WhatsApp] From: {phone_number} - Message: {message_text}")
        contenido = message_text
        location = None
    elif message_type == "location":
        ubicacion = message.get("location", {})
        latitude = ubicacion.get("latitude")
        longitude = ubicacion.get("longitude")
        print(f"[WhatsApp] Location from {phone_number}: lat={latitude}, lon={longitude}")
        message_text = None
        contenido = f"lat={latitude}, lon={longitude}"
        location = {"latitude": latitude, "longitude": longitude}
    else:
        print(f"[WhatsApp] Tipo de mensaje no soportado ({message_type}) de {phone_number}")
        message_text = None
        contenido = None
        location = None

    es_nuevo = await registrar_mensaje_entrante(
        telefono=phone_number,
        tipo=message_type or "desconocido",
        contenido=contenido,
        meta_message_id=wamid,
    )
    if not es_nuevo:
        logger.info("[WhatsApp] Mensaje %s de %s ya procesado: se ignora", wamid, phone_number)
        return

    if message_type not in ("text", "location"):
        await send_whatsapp_message(to=phone_number, message=MENSAJE_TIPO_NO_SOPORTADO)
        return

    if not await esta_activa(phone_number):
        await _enrutar_a_ejecutiva(phone_number)
        return

    try:
        respuesta = await procesar_mensaje(
            phone=phone_number,
            message_type=message_type,
            message_text=message_text,
            location=location,
        )
    except Exception:
        logger.exception(
            "[WhatsApp] Falló procesar_mensaje (%s) para %s", message_type, phone_number
        )
        respuesta = MENSAJE_ERROR_GENERICO

    await send_whatsapp_message(to=phone_number, message=respuesta)


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Responde 200 a Meta de inmediato: si la respuesta tarda (LLM + BD),
    Meta reintenta la entrega y el mismo mensaje se procesaría varias veces.
    El trabajo real queda en la cola FIFO del teléfono (webhook_queue), que
    respeta el orden de llegada y no procesa dos mensajes del mismo teléfono
    en paralelo. Una entrega repetida (mismo wamid) se descarta aquí mismo
    y, si el proceso se reinició entremedio, en registrar_mensaje_entrante."""
    payload = await request.json()

    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        phone_number = message["from"]
    except (KeyError, IndexError):
        print(f"[WhatsApp] Payload without message data: {payload}")
        return {"status": "received"}

    wamid = message.get("id")
    if wamid and not reservar_wamid(wamid):
        logger.info("[WhatsApp] Entrega repetida del mensaje %s de %s: se ignora", wamid, phone_number)
        return {"status": "received"}

    encolar(phone_number, lambda: _procesar_mensaje_entrante(message))
    return {"status": "received"}
