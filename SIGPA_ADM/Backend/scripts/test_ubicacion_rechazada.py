"""
Script temporal para simular, a través de procesar_mensaje, el camino
alternativo de un cliente NUEVO que rechaza compartir su ubicación de
WhatsApp (ver "ubicacion_rechazada" en el SYSTEM_PROMPT de
app/services/agent_service.py y las salvaguardas correspondientes en
app/services/order_flow.py).
No es parte del código final: solo para validar manualmente el comportamiento.

Cada caso usa un teléfono nuevo y distinto (ninguno debe existir todavía como
Cliente en la BD, ya que procesar_mensaje determina es_cliente_nuevo
consultando por telefono == PHONE). Como ningún caso llega a confirmar el
pedido (_confirmar_pedido nunca se llama aquí), no se crea ningún Cliente en
la BD al correr este script, así que puede repetirse sin "ensuciar" los
teléfonos usados.

Cada conversación llega primero hasta el estado "esperando_ubicacion" con un
turno inicial ("Quiero 1 Dispensador USB, soy <Nombre>"), y luego envía el
mensaje de prueba correspondiente para observar si el bot:
  - sigue pidiendo la ubicación retomando el pedido en curso (saludo o
    interjección ambigua, sin rechazo ni cancelación), o
  - avanza aceptando la dirección en texto (detectó rechazo + dirección), o
  - pide la dirección en texto en vez de insistir en la ubicación (detectó
    rechazo pero todavía no tiene dirección), o
  - vacía el pedido por completo (cancelación explícita).

Uso: python -m scripts.test_ubicacion_rechazada
"""

import asyncio

from app.services.draft_store import get_draft
from app.services.order_flow import _es_cliente_nuevo, procesar_mensaje

MENSAJE_INICIAL = "Quiero 1 Dispensador USB, soy Pedro"
PRODUCTOS_ESPERADOS = [{"nombre_producto": "Dispensador USB", "cantidad": 1}]


def _avanzo(draft: dict | None) -> bool:
    """True si el draft ya salió del estado 'esperando_ubicacion' (es decir,
    el pedido avanzó sin necesidad de recibir coordenadas)."""
    return draft is not None and draft.get("estado") != "esperando_ubicacion"


def _retomo_pedido_sin_perderlo(draft: dict | None) -> bool:
    """True si, tras un saludo/interjección ambigua, el draft sigue esperando
    ubicación Y conservó los productos del pedido en curso (no se vació ni se
    marcó como rechazo)."""
    return (
        draft is not None
        and draft.get("estado") == "esperando_ubicacion"
        and draft.get("productos") == PRODUCTOS_ESPERADOS
        and not draft.get("ubicacion_rechazada")
        and draft.get("direccion_texto") is None
    )


CASOS = [
    {
        "descripcion": '"hola?" no es un rechazo ni una cancelación: debe retomar el pedido y seguir pidiendo ubicación',
        "phone": "56930000001",
        "mensaje": "hola?",
        "verificar": lambda draft, respuesta: _retomo_pedido_sin_perderlo(draft),
        "esperado": "sigue esperando ubicación, conserva productos, ubicacion_rechazada=false",
    },
    {
        "descripcion": '"ok" no es un rechazo ni una cancelación: debe retomar el pedido y seguir pidiendo ubicación',
        "phone": "56930000002",
        "mensaje": "ok",
        "verificar": lambda draft, respuesta: _retomo_pedido_sin_perderlo(draft),
        "esperado": "sigue esperando ubicación, conserva productos, ubicacion_rechazada=false",
    },
    {
        "descripcion": "Rechazo explícito + dirección en el mismo mensaje: debe avanzar",
        "phone": "56930000003",
        "mensaje": "no tengo esa opción, mi dirección es Av. Siempre Viva 742",
        "verificar": lambda draft, respuesta: (
            _avanzo(draft)
            and draft.get("ubicacion_rechazada") is True
            and draft.get("direccion_texto") == "Av. Siempre Viva 742"
        ),
        "esperado": "avanza con direccion_texto='Av. Siempre Viva 742', ubicacion_rechazada=true",
    },
    {
        "descripcion": "Rechazo implícito ('prefiero escribirla') + dirección: debe avanzar",
        "phone": "56930000004",
        "mensaje": "prefiero escribirla: Calle Falsa 123",
        "verificar": lambda draft, respuesta: (
            _avanzo(draft)
            and draft.get("ubicacion_rechazada") is True
            and draft.get("direccion_texto") == "Calle Falsa 123"
        ),
        "esperado": "avanza con direccion_texto='Calle Falsa 123', ubicacion_rechazada=true",
    },
    {
        "descripcion": "Rechazo sin dirección alternativa: debe pedir la dirección en texto, no la ubicación",
        "phone": "56930000005",
        "mensaje": "no puedo",
        "verificar": lambda draft, respuesta: (
            draft is not None
            and draft.get("ubicacion_rechazada") is True
            and draft.get("estado") != "esperando_ubicacion"
            and draft.get("direccion_texto") is None
            and "ubicación de whatsapp" not in respuesta.lower()
            and "📎" not in respuesta
        ),
        "esperado": "ubicacion_rechazada=true, direccion_texto sigue null, respuesta pide dirección (no ubicación)",
    },
    {
        "descripcion": '"sigues ahí?" no es un rechazo ni una cancelación: debe retomar el pedido y seguir pidiendo ubicación',
        "phone": "56930000006",
        "mensaje": "sigues ahí?",
        "verificar": lambda draft, respuesta: _retomo_pedido_sin_perderlo(draft),
        "esperado": "sigue esperando ubicación, conserva productos, ubicacion_rechazada=false",
    },
    {
        "descripcion": '"cancela" SÍ debe vaciar el pedido en curso por completo',
        "phone": "56930000007",
        "mensaje": "cancela",
        "verificar": lambda draft, respuesta: (
            draft is None and "cancel" in respuesta.lower()
        ),
        "esperado": "el draft queda en None (pedido cancelado por completo)",
    },
]


async def _llegar_a_esperando_ubicacion(phone: str) -> tuple[str, dict | None]:
    respuesta = await procesar_mensaje(
        phone=phone,
        message_type="text",
        message_text=MENSAJE_INICIAL,
        location=None,
    )
    draft = get_draft(phone)
    return respuesta, draft


async def main() -> None:
    resultados = []

    for i, caso in enumerate(CASOS, start=1):
        phone = caso["phone"]
        print("#" * 70)
        print(f"Caso {i}/{len(CASOS)}: {caso['descripcion']}")
        print(f"Phone: {phone}")
        print("#" * 70)

        if not await _es_cliente_nuevo(phone):
            print(
                f"[ADVERTENCIA] {phone} ya existe como Cliente en la BD; "
                "este caso necesita un teléfono nuevo para probar el flujo "
                "de cliente nuevo. Cambia el número en CASOS y vuelve a correr."
            )
            resultados.append((caso["descripcion"], False))
            print()
            continue

        print("--- Turno 1 (llega hasta 'esperando_ubicacion') ---")
        print(f"Cliente: {MENSAJE_INICIAL}")
        respuesta_inicial, draft_inicial = await _llegar_a_esperando_ubicacion(phone)
        print(f"Bot: {respuesta_inicial}")

        if draft_inicial is None or draft_inicial.get("estado") != "esperando_ubicacion":
            print(
                "[ERROR] No se alcanzó el estado 'esperando_ubicacion' tras el turno "
                f"inicial (estado actual: {draft_inicial.get('estado') if draft_inicial else None}). "
                "No se puede probar este caso."
            )
            resultados.append((caso["descripcion"], False))
            print()
            continue

        print()
        print("--- Turno 2 (mensaje de prueba) ---")
        print(f"Cliente: {caso['mensaje']}")
        respuesta = await procesar_mensaje(
            phone=phone,
            message_type="text",
            message_text=caso["mensaje"],
            location=None,
        )
        draft = get_draft(phone)
        print(f"Bot: {respuesta}")
        print(f"Draft resultante: {draft}")

        avanzo = _avanzo(draft)
        print(f"¿El pedido avanzó (ya no está en 'esperando_ubicacion')?: {avanzo}")
        print(f"Esperado: {caso['esperado']}")

        try:
            ok = bool(caso["verificar"](draft, respuesta))
        except Exception as exc:
            print(f"[ERROR] Falló la verificación del caso: {exc!r}")
            ok = False

        print(f"Resultado: {'OK' if ok else 'FALLO'}")
        resultados.append((caso["descripcion"], ok))
        print()

    print("=" * 70)
    print("Resumen final:")
    for descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
