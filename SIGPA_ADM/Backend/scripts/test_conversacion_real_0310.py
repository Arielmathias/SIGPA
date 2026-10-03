"""
Script temporal que reproduce, a través del webhook y de procesar_mensaje y
contra la BD, la conversación real del 2026-10-03 (teléfono 56957721243,
pedido #29) y valida las correcciones de sus 4 bugs:

1. Un mismo mensaje entregado varias veces por Meta (mismo wamid) generaba
   varias respuestas: ahora se procesa una sola vez.
2. "4 bidones de 20" + "2 recargas y 2 nuevos" + "un dispensador usb"
   dejaba el pedido solo con el dispensador: ahora los productos se suman.
3. "no", "no", "canelar", "si" ante "¿Confirmas?" terminaba confirmando el
   pedido: ahora "no" pregunta qué cambiar, "canelar" cancela y un "si"
   tras un "no" no confirma.
4. "no cancelen mi pedido" no debe cancelar.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: todos los teléfonos usados están en el rango 56932100xxx.
Al empezar y al terminar, el script BORRA de ese rango los clientes (con sus
pedidos y detalles), los mensajes de mensaje_whatsapp y las filas de
conversacion_bot. No toca ningún otro cliente ni pedido. Las filas de
auditoria no se borran (son el registro histórico).

Nunca envía mensajes reales: el caso del webhook reemplaza
send_whatsapp_message por un fake, y los demás casos llaman directo a
procesar_mensaje (que devuelve el texto en vez de enviarlo).

Uso: python -m scripts.test_conversacion_real_0310
"""

import asyncio
import logging
import random
import time
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.api.routes.whatsapp as whatsapp_route
from app.core.database import SessionLocal
from app.main import app
from app.services import webhook_queue
from app.services.conversacion_bot_service import marcar_activa
from app.services.mensaje_whatsapp_service import (
    INDICE_WAMID_ENTRANTE,
    registrar_mensaje_entrante,
)
from app.services.draft_store import clear_draft, get_draft, save_draft
from app.services.order_flow import (
    MENSAJE_NO_CONFIRMADO,
    MENSAJE_PEDIDO_CANCELADO,
    PREFIJO_REPETIR_RESUMEN,
    PREGUNTA_CANCELAR,
    _cantidades_por_variante,
    _datos_cliente,
    _intencion_cancelar,
    _responder_siguiente_paso,
    procesar_mensaje,
)
from app.models import Cliente

RANGO_TELEFONOS_SQL = r"^(56)?932100\d{3}$"

CLIENTE = {
    "telefono": "56932100001",
    "nombre": "Felipe Prueba 0310",
    "direccion": "Santa Maria 793",
    "latitud": Decimal("-33.122910"),
    "longitud": Decimal("-71.570862"),
}

TELEFONO_WEBHOOK = "56932100002"

TELEFONOS_PRUEBA = (CLIENTE["telefono"], TELEFONO_WEBHOOK)

LINEAS_BIDONES = [
    {"nombre_producto": "Bidón 20L Recarga", "cantidad": 2},
    {"nombre_producto": "Bidón 20L Nuevo", "cantidad": 2},
]
LINEAS_PEDIDO = [*LINEAS_BIDONES, {"nombre_producto": "Dispensador USB", "cantidad": 1}]


# --------------------------------------------------------------------------
# Helpers de BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar_rango() -> None:
    ids_sql = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    await _ejecutar(
        f"delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id in ({ids_sql}))",
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_sql})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from mensaje_whatsapp where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar("delete from conversacion_bot where telefono ~ :rango", rango=RANGO_TELEFONOS_SQL)


async def _crear_cliente() -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        **CLIENTE,
    )
    return result.scalar_one()


async def _pedidos_de(cliente_id: int) -> list[dict]:
    result = await _ejecutar("select * from pedido where cliente_id = :id order by id", id=cliente_id)
    return [dict(fila._mapping) for fila in result]


async def _detalles_de_cliente(cliente_id: int) -> list[dict]:
    result = await _ejecutar(
        "select d.*, p.nombre from detalle_pedido d join producto p on p.id = d.producto_id "
        "where d.pedido_id in (select id from pedido where cliente_id = :id) order by d.id",
        id=cliente_id,
    )
    return [dict(fila._mapping) for fila in result]


async def _precios() -> dict[str, Decimal]:
    result = await _ejecutar("select nombre, precio_unitario from producto")
    return {fila.nombre: fila.precio_unitario for fila in result}


async def _cliente_orm(cliente_id: int) -> Cliente:
    async with SessionLocal() as session:
        return await session.get(Cliente, cliente_id)


def _clp(valor) -> str:
    return f"${int(valor):,.0f}".replace(",", ".")


# --------------------------------------------------------------------------
# Conversación
# --------------------------------------------------------------------------


async def _conversar(phone: str, turnos: list[str]) -> list[str]:
    respuestas = []
    for turno in turnos:
        print(f"  Cliente: {turno}")
        respuesta = await procesar_mensaje(phone, "text", turno, None)
        draft = get_draft(phone) or {}
        print(f"  Bot [{draft.get('paso')}/{draft.get('estado')}]: {respuesta}")
        respuestas.append(respuesta)
    return respuestas


async def _preparar_resumen(ctx: dict) -> str:
    """Deja el draft del cliente de prueba esperando confirmación del
    resumen (bidones + dispensador, dirección registrada), armado por el
    mismo código que usa la conversación (_responder_siguiente_paso)."""
    phone = CLIENTE["telefono"]
    draft = {
        "intencion": "pedido",
        "productos": [dict(item) for item in LINEAS_PEDIDO],
        "aclaracion_pendiente": None,
        "notas": None,
        "nombre_cliente": None,
        "usa_direccion_habitual": True,
        "direccion_texto": None,
        "ubicacion": None,
        "ubicacion_rechazada": False,
        "algo_mas_respondido": True,
        "unidades_pedidas": {"Bidón 20L": 4, "Dispensador USB": 1},
    }
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    paso, texto = await _responder_siguiente_paso(phone, draft, cliente)
    assert paso == "confirmacion", f"no se llegó al resumen: {paso} / {texto}"
    print(f"  [estado preparado] Bot: {texto}")
    return texto


def _es_resumen(texto: str) -> bool:
    return "Resumen de tu pedido" in texto and "¿Confirmas el pedido?" in texto


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


def _payload(phone: str, wamid: str, texto: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_TEST",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"profile": {"name": "Prueba"}, "wa_id": phone}],
                            "messages": [
                                {
                                    "from": phone,
                                    "id": wamid,
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": texto},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


async def caso_a(ctx: dict, v: Verificador) -> None:
    phone = TELEFONO_WEBHOOK
    await marcar_activa(phone)
    enviados = []

    async def fake_send(to: str, message: str):
        enviados.append((to, message))
        print(f"  [fake send] a {to}: {message}")
        return {"messages": [{"id": f"wamid.fake-{len(enviados)}"}]}

    original = whatsapp_route.send_whatsapp_message
    whatsapp_route.send_whatsapp_message = fake_send
    try:
        wamid = f"wamid.test-0310-{uuid.uuid4().hex}"
        payload = _payload(phone, wamid, "hola")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def post():
                inicio = time.perf_counter()
                respuesta = await client.post("/webhook", json=payload)
                return respuesta.status_code, time.perf_counter() - inicio

            resultados = await asyncio.gather(post(), post(), post())
            print(f"  3 entregas del mismo wamid: {resultados}")
            v.check(all(status == 200 for status, _ in resultados), "las 3 entregas reciben 200")
            v.check(
                all(duracion < 1.0 for _, duracion in resultados),
                "el webhook responde de inmediato (< 1 s, sin esperar al LLM)",
            )

            await webhook_queue.esperar_pendientes()
            v.check(len(enviados) == 1, f"se envía una sola respuesta (enviadas: {len(enviados)})")
            filas = await _ejecutar(
                "select count(*) from mensaje_whatsapp where meta_message_id = :w", w=wamid
            )
            v.check(filas.scalar_one() == 1, "el mensaje entrante queda registrado una sola vez, con su wamid")

            # Reintento de Meta después de un reinicio del proceso (se pierde
            # el registro en memoria): lo descarta la BD.
            webhook_queue.olvidar_wamids()
            status, _ = await post()
            await webhook_queue.esperar_pendientes()
            v.check(status == 200 and len(enviados) == 1, "tras un 'reinicio', el reintento tampoco se procesa (dedup en BD)")
    finally:
        whatsapp_route.send_whatsapp_message = original
        clear_draft(phone)

    # Orden por teléfono: tareas del mismo teléfono en orden de llegada y sin
    # solaparse, aunque cada una tarde distinto.
    orden, activas, solapadas = [], set(), []

    def tarea(phone_tarea: str, n: int):
        async def correr():
            if phone_tarea in activas:
                solapadas.append(n)
            activas.add(phone_tarea)
            await asyncio.sleep(random.uniform(0, 0.05))
            orden.append((phone_tarea, n))
            activas.discard(phone_tarea)
        return correr

    for n in range(6):
        webhook_queue.encolar("cola-a", tarea("cola-a", n))
        webhook_queue.encolar("cola-b", tarea("cola-b", n))
    await webhook_queue.esperar_pendientes()
    v.check([n for p, n in orden if p == "cola-a"] == list(range(6)), "cola por teléfono respeta el orden de llegada")
    v.check(not solapadas, "nunca se procesan dos mensajes del mismo teléfono en paralelo")


async def caso_b(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(phone, ["quiero 4 bidones de 20"])
    draft = get_draft(phone) or {}
    v.check(
        draft.get("aclaracion_pendiente") == {"capacidad_litros": 20, "cantidad": 4},
        "queda pendiente '¿nuevo o recarga?' por 4 bidones de 20L",
    )
    v.check(draft.get("paso") == "producto", "sigue en el paso 'producto'")

    respuestas += await _conversar(phone, ["2 recargas y 2 nuevos"])
    draft = get_draft(phone) or {}
    v.check(draft.get("productos") == LINEAS_BIDONES, "draft con dos líneas: 2x Bidón 20L Recarga y 2x Bidón 20L Nuevo")
    v.check(draft.get("aclaracion_pendiente") is None, "sin aclaración pendiente")
    v.check(
        draft.get("paso") == "confirmar_direccion" and CLIENTE["direccion"] in respuestas[-1],
        "avanza a confirmar la dirección registrada",
    )


async def caso_c(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    # Los turnos de b se repiten porque cada caso parte con el draft limpio.
    await _conversar(phone, ["quiero 4 bidones de 20", "2 recargas y 2 nuevos"])
    respuestas = await _conversar(phone, ["si", "si, un dispensador usb", "no"])
    draft = get_draft(phone) or {}
    precios = ctx["precios"]
    total = sum(precios[item["nombre_producto"]] * item["cantidad"] for item in LINEAS_PEDIDO)
    resumen = respuestas[-1]
    v.check("algo más" in respuestas[0].lower(), "tras confirmar la dirección pregunta '¿algo más?'")
    v.check(draft.get("productos") == LINEAS_PEDIDO, "draft con bidones + dispensador (nada se perdió)")
    v.check(_es_resumen(resumen), "muestra el resumen")
    v.check(
        all(f"{item['cantidad']}x {item['nombre_producto']}" in resumen for item in LINEAS_PEDIDO),
        "el resumen incluye las 3 líneas",
    )
    v.check(f"Total: {_clp(total)}" in resumen, f"total correcto ({_clp(total)})")
    v.check(draft.get("estado") == "esperando_confirmacion", "estado esperando_confirmacion")
    clear_draft(phone)


async def caso_d(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["no"])
    v.check(respuestas[0] == MENSAJE_NO_CONFIRMADO, "responde 'Entendido, no lo confirmo. ¿Qué quieres cambiar...?'")
    v.check(not _es_resumen(respuestas[0]), "no repite el resumen")
    v.check((get_draft(phone) or {}).get("estado") == "esperando_modificacion", "estado esperando_modificacion")
    v.check((get_draft(phone) or {}).get("productos") == LINEAS_PEDIDO, "el pedido en curso se conserva")
    clear_draft(phone)


async def caso_e(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["no", "no", "canelar"])
    v.check(respuestas[0] == MENSAJE_NO_CONFIRMADO, "1er 'no': pregunta qué cambiar o si cancela")
    v.check(respuestas[1] == PREGUNTA_CANCELAR, "2do 'no': pregunta si quiere CANCELAR")
    v.check(respuestas[2] == MENSAJE_PEDIDO_CANCELADO, "'canelar' (errata) cancela el pedido")
    v.check(get_draft(phone) is None, "draft vaciado por completo")
    respuestas += await _conversar(phone, ["si"])
    v.check("confirmado" not in respuestas[3].lower(), "el 'si' final no confirma nada")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no queda ningún pedido del cliente")
    v.check(not await _detalles_de_cliente(ctx["cliente_id"]), "no queda ningún detalle_pedido del cliente")
    clear_draft(phone)


async def caso_f(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["no", "si"])
    v.check(respuestas[0] == MENSAJE_NO_CONFIRMADO, "'no' pregunta qué cambiar")
    v.check(
        respuestas[1].startswith(PREFIJO_REPETIR_RESUMEN) and _es_resumen(respuestas[1]),
        "'si' tras el 'no' vuelve a mostrar el resumen y pide confirmar",
    )
    v.check("confirmado" not in respuestas[1].lower(), "ese 'si' no confirma")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no se creó pedido")
    v.check((get_draft(phone) or {}).get("estado") == "esperando_confirmacion", "queda esperando confirmación explícita")
    clear_draft(phone)


async def caso_g(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    await _preparar_resumen(ctx)
    respuestas = await _conversar(phone, ["no cancelen mi pedido"])
    draft = get_draft(phone)
    v.check(respuestas[0] != MENSAJE_PEDIDO_CANCELADO and respuestas[0] != PREGUNTA_CANCELAR, "no cancela ni pregunta si cancelar")
    v.check(draft is not None and draft.get("productos") == LINEAS_PEDIDO, "el pedido en curso se conserva")
    v.check(not await _pedidos_de(ctx["cliente_id"]), "no confirma el pedido")
    clear_draft(phone)


async def caso_h(ctx: dict, v: Verificador) -> None:
    phone = CLIENTE["telefono"]
    respuestas = await _conversar(
        phone, ["quiero 4 bidones de 20", "2 recargas y 2 nuevos", "si", "si, un dispensador usb", "no"]
    )
    v.check(_es_resumen(respuestas[-1]), "llega al resumen")
    respuestas += await _conversar(phone, ["si"])
    pedidos = await _pedidos_de(ctx["cliente_id"])
    v.check(len(pedidos) == 1 and "confirmado" in respuestas[-1].lower(), "'si' al resumen crea el pedido")
    detalles = await _detalles_de_cliente(ctx["cliente_id"])
    v.check(
        sorted((d["nombre"], d["cantidad_solicitada"]) for d in detalles)
        == sorted((item["nombre_producto"], item["cantidad"]) for item in LINEAS_PEDIDO),
        "detalle_pedido con las 3 líneas y sus cantidades",
    )
    precios = ctx["precios"]
    total = sum(precios[item["nombre_producto"]] * item["cantidad"] for item in LINEAS_PEDIDO)
    v.check(bool(pedidos) and pedidos[0]["total"] == total, f"total del pedido {_clp(total)}")
    v.check(get_draft(phone) is None, "draft limpio tras confirmar")
    # Limpia el pedido de prueba para que los casos siguientes partan sin pedidos.
    await _ejecutar(
        "delete from detalle_pedido where pedido_id in (select id from pedido where cliente_id = :id)",
        id=ctx["cliente_id"],
    )
    await _ejecutar("delete from pedido where cliente_id = :id", id=ctx["cliente_id"])


async def caso_i(ctx: dict, v: Verificador) -> None:
    """Salvaguarda: si el draft tiene menos productos de los que el cliente
    pidió (lo que pasó con el pedido #29), no se muestra el resumen."""
    phone = CLIENTE["telefono"]
    draft = {
        "intencion": "pedido",
        "productos": [{"nombre_producto": "Dispensador USB", "cantidad": 1}],
        "aclaracion_pendiente": None,
        "usa_direccion_habitual": True,
        "algo_mas_respondido": True,
        "unidades_pedidas": {"Bidón 20L": 4, "Dispensador USB": 1},
    }
    cliente = _datos_cliente(await _cliente_orm(ctx["cliente_id"]))
    paso, texto = await _responder_siguiente_paso(phone, draft, cliente)
    print(f"  Bot [{paso}]: {texto}")
    v.check(paso != "confirmacion" and not _es_resumen(texto), "no muestra el resumen incompleto")
    v.check("4x Bidón 20L" in texto, "le dice qué productos no quedaron registrados")
    respuestas = await _conversar(phone, ["no"])
    v.check(_es_resumen(respuestas[0]), "si responde que no quiere agregar nada, recién ahí muestra el resumen")
    clear_draft(phone)


async def caso_j(ctx: dict, v: Verificador) -> None:
    """Detección determinista (sin LLM) de cancelación y de variantes."""
    esperados_cancelar = {
        "cancelar": "cancelar",
        "CANCELAR": "cancelar",
        "canelar": "cancelar",
        "anula el pedido": "cancelar",
        "no, cancela": "cancelar",
        "no cancelen mi pedido": None,
        "no me lo cancelen": None,
        "nunca anulen mi pedido": None,
        "no puedo compartirla desde este celular": None,
        "cancela el dispensador": "duda",
        "oye por favor canelar todo lo que te pedi": "duda",
        "hola": None,
    }
    for mensaje, esperado in esperados_cancelar.items():
        v.check(_intencion_cancelar(mensaje) == esperado, f"cancelación {mensaje!r} -> {esperado}")
    v.check(_intencion_cancelar("Los Canelos 345", texto_libre=True) is None, "'Los Canelos 345' en paso dirección no cancela")
    v.check(_intencion_cancelar("Manuela", texto_libre=True) is None, "'Manuela' en paso nombre no cancela")
    esperados_variantes = {
        "2 recargas y 2 nuevos": {"Recarga": 2, "Nuevo": 2},
        "dos nuevos y dos recargas": {"Nuevo": 2, "Recarga": 2},
        "1 nuevo y el resto recarga": {"Nuevo": 1, "Recarga": None},
        "3 de 20 recarga": {"Recarga": 3},
        "recarga": {"Recarga": None},
    }
    for mensaje, esperado in esperados_variantes.items():
        v.check(_cantidades_por_variante(mensaje, 20) == esperado, f"variantes {mensaje!r} -> {esperado}")
    v.check(
        _cantidades_por_variante("necesito 2 bidones de 20 litros recarga y 1 de 12 nuevo", 20) == {"Recarga": 2},
        "'1 de 12 nuevo' no se cuenta como 12 bidones de 20L",
    )

    # Un pedido en curso + "2 recargas" de 4 pendientes: no avanza a la
    # dirección hasta resolver los 2 que faltan.
    phone = CLIENTE["telefono"]
    save_draft(
        phone,
        {
            "intencion": "pedido",
            "productos": [],
            "aclaracion_pendiente": {"capacidad_litros": 20, "cantidad": 4},
            "paso": "producto",
            "estado": "armando",
        },
    )
    await _conversar(phone, ["2 recargas"])
    draft = get_draft(phone) or {}
    v.check(
        draft.get("productos") == [{"nombre_producto": "Bidón 20L Recarga", "cantidad": 2}]
        and draft.get("aclaracion_pendiente") == {"capacidad_litros": 20, "cantidad": 2}
        and draft.get("paso") == "producto",
        "con 2 de 4 resueltos, sigue preguntando por los 2 restantes",
    )
    clear_draft(phone)


class _CapturaErrores(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.registros = []

    def emit(self, record):
        self.registros.append(record)


async def caso_k(ctx: dict, v: Verificador) -> None:
    """El índice único uq_mensaje_whatsapp_entrante_meta_message_id: una
    violación es "mensaje duplicado" (no se procesa, no se responde, 200),
    nunca un error."""
    phone = TELEFONO_WEBHOOK

    # 1. El índice existe y rechaza el mismo wamid entrante dos veces.
    wamid_sql = f"wamid.test-0310-sql-{uuid.uuid4().hex}"
    insertar = (
        "insert into mensaje_whatsapp (telefono, meta_message_id, direccion, tipo, contenido, estado) "
        "values (:t, :w, 'entrante', 'text', 'hola', 'recibido')"
    )
    await _ejecutar(insertar, t=phone, w=wamid_sql)
    try:
        await _ejecutar(insertar, t=phone, w=wamid_sql)
        violacion = None
    except IntegrityError as exc:
        violacion = str(exc.orig)
    v.check(
        violacion is not None and INDICE_WAMID_ENTRANTE in violacion,
        "insertar dos veces el mismo wamid entrante viola el índice único",
    )

    # 2. registrar_mensaje_entrante traduce esa violación a "duplicado".
    captura = _CapturaErrores()
    logging.getLogger("app").addHandler(captura)
    try:
        wamid = f"wamid.test-0310-dup-{uuid.uuid4().hex}"
        primero = await registrar_mensaje_entrante(phone, "text", "hola", wamid)
        segundo = await registrar_mensaje_entrante(phone, "text", "hola", wamid)
        v.check(primero is True and segundo is False, "1ra vez se procesa, 2da es duplicado")

        wamid_carrera = f"wamid.test-0310-race-{uuid.uuid4().hex}"
        resultados = await asyncio.gather(
            *(registrar_mensaje_entrante(phone, "text", "hola", wamid_carrera) for _ in range(3))
        )
        v.check(sorted(resultados) == [False, False, True], "3 registros simultáneos: solo uno se procesa")

        # 3. Por el webhook, con el registro en memoria perdido (reinicio):
        # 200, sin procesar ni responder.
        enviados, procesados = [], []

        async def fake_send(to: str, message: str):
            enviados.append((to, message))

        async def espia_procesar(**kwargs):
            procesados.append(kwargs)
            return "no debería llamarse"

        originales = (whatsapp_route.send_whatsapp_message, whatsapp_route.procesar_mensaje)
        whatsapp_route.send_whatsapp_message = fake_send
        whatsapp_route.procesar_mensaje = espia_procesar
        try:
            webhook_queue.olvidar_wamids()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                respuesta = await client.post("/webhook", json=_payload(phone, wamid, "hola"))
            await webhook_queue.esperar_pendientes()
        finally:
            whatsapp_route.send_whatsapp_message, whatsapp_route.procesar_mensaje = originales

        v.check(respuesta.status_code == 200, "el webhook devuelve 200")
        v.check(not procesados and not enviados, "no procesa ni responde el duplicado")
        filas = await _ejecutar("select count(*) from mensaje_whatsapp where meta_message_id = :w", w=wamid)
        v.check(filas.scalar_one() == 1, "sigue habiendo una sola fila con ese wamid")
    finally:
        logging.getLogger("app").removeHandler(captura)
    v.check(not captura.registros, "ningún error logueado (la violación no se trata como falla)")


CASOS = [
    ("a", "Mismo wamid entregado 3 veces: se procesa y responde una sola vez; orden por teléfono", caso_a),
    ("b", "'4 bidones de 20' + '2 recargas y 2 nuevos': dos líneas y avanza a la dirección", caso_b),
    ("c", "Tras b: dirección, 'si, un dispensador usb', 'no': resumen con todo y total correcto", caso_c),
    ("d", "'no' ante '¿Confirmas?': pregunta qué cambiar, no repite el resumen", caso_d),
    ("e", "'no', 'no', 'canelar', 'si': pedido cancelado, sin pedido ni detalle en la BD", caso_e),
    ("f", "'no' y luego 'si': no confirma, vuelve al resumen", caso_f),
    ("g", "'no cancelen mi pedido' no cancela", caso_g),
    ("h", "Confirmación feliz: 'si' al resumen crea el pedido con todas las líneas", caso_h),
    ("i", "Draft con menos productos de los pedidos: no muestra el resumen", caso_i),
    ("j", "Detección determinista de cancelación y variantes; '2 recargas' de 4 no avanza", caso_j),
    ("k", "Índice único de wamid: la violación es 'duplicado' (200, sin procesar ni responder)", caso_k),
]


async def main() -> None:
    await _limpiar_rango()
    ctx = {"cliente_id": await _crear_cliente(), "precios": await _precios()}
    print(f"Cliente de prueba: id={ctx['cliente_id']} {CLIENTE}")

    resultados = []
    try:
        for letra, descripcion, caso in CASOS:
            print("#" * 70)
            print(f"Caso {letra}: {descripcion}")
            print("#" * 70)
            for phone in TELEFONOS_PRUEBA:
                clear_draft(phone)
            v = Verificador()
            try:
                await caso(ctx, v)
            except Exception as exc:
                print(f"    [ERROR] {exc!r}")
                v.errores.append(repr(exc))
            resultados.append((letra, descripcion, not v.errores))
            print()
    finally:
        for phone in TELEFONOS_PRUEBA:
            clear_draft(phone)
        await _limpiar_rango()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
