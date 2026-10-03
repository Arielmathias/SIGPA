"""
Script temporal para validar la generación de la ruta de reparto (EP-04):
GET /rutas/pedidos-pendientes y POST /rutas/planificar (ver
app/services/ruta_service.py), contra la BD y con n8n SIMULADO (nunca se
llama al webhook real: ruta_service._cliente_http se reemplaza por un cliente
con httpx.MockTransport). La autenticación JWT del panel se reemplaza con
dependency_overrides.
No es parte del código final: solo para validar manualmente el comportamiento.

Datos de prueba: clientes en el rango de teléfonos 56932300xxx con sus
pedidos. Al empezar y al terminar se borran esos clientes, sus pedidos y
detalles, y las filas de auditoria que generó este script (usuario
USUARIO_PRUEBA). No toca ningún otro cliente ni pedido.

Uso: python -m scripts.test_rutas
"""

import asyncio
import json
from decimal import Decimal

import httpx
from sqlalchemy import text

import app.services.ruta_service as ruta_service
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.main import app

RANGO_TELEFONOS_SQL = r"^(56)?932300\d{3}$"
USUARIO_PRUEBA = "test-rutas@sigpa.local"
URL_N8N_PRUEBA = "http://n8n.test/webhook/sigpa-ruta"
SECRETO_N8N_PRUEBA = "secreto-de-prueba"

CLIENTE_CON_COORDS = {
    "nombre": "Ruta Con Coords",
    "telefono": "56932300001",
    "direccion": "Av. Libertad 100, Viña del Mar",
    "latitud": Decimal("-33.024500"),
    "longitud": Decimal("-71.551800"),
}
CLIENTE_SIN_COORDS = {
    "nombre": "Ruta Sin Coords",
    "telefono": "56932300002",
    "direccion": "Los Pinos 456, Quilpué",
    "latitud": None,
    "longitud": None,
}


# --------------------------------------------------------------------------
# BD
# --------------------------------------------------------------------------


async def _ejecutar(sql: str, **params):
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        return result


async def _limpiar() -> None:
    ids_clientes = "select id from cliente where regexp_replace(coalesce(telefono,''), '\\D', '', 'g') ~ :rango"
    ids_pedidos = f"select id from pedido where cliente_id in ({ids_clientes})"
    await _ejecutar(
        f"delete from auditoria where entidad = 'pedido' and usuario = :usuario and entidad_id in ({ids_pedidos})",
        usuario=USUARIO_PRUEBA,
        rango=RANGO_TELEFONOS_SQL,
    )
    await _ejecutar(f"delete from detalle_pedido where pedido_id in ({ids_pedidos})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from pedido where cliente_id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)
    await _ejecutar(f"delete from cliente where id in ({ids_clientes})", rango=RANGO_TELEFONOS_SQL)


async def _crear_cliente(datos: dict) -> int:
    result = await _ejecutar(
        "insert into cliente (nombre, telefono, direccion, latitud, longitud) "
        "values (:nombre, :telefono, :direccion, :latitud, :longitud) returning id",
        **datos,
    )
    return result.scalar_one()


async def _crear_pedido(cliente: dict, cliente_id: int, estado: str, orden: int | None = None, con_coords=True) -> int:
    result = await _ejecutar(
        "insert into pedido (cliente_id, estado, direccion_despacho, total, latitud, longitud, orden_entrega) "
        "values (:cliente_id, cast(:estado as estado_pedido), :direccion, 1000, :latitud, :longitud, :orden) returning id",
        cliente_id=cliente_id,
        estado=estado,
        direccion=cliente["direccion"],
        latitud=cliente["latitud"] if con_coords else None,
        longitud=cliente["longitud"] if con_coords else None,
        orden=orden,
    )
    return result.scalar_one()


async def _pedido(pid: int) -> dict | None:
    fila = (await _ejecutar("select * from pedido where id = :id", id=pid)).first()
    return dict(fila._mapping) if fila else None


async def _cliente(cid: int) -> dict:
    fila = (await _ejecutar("select direccion, latitud, longitud from cliente where id = :id", id=cid)).first()
    return dict(fila._mapping)


async def _reiniciar(ctx: dict) -> None:
    """Deja los pedidos planificables de prueba como recién creados."""
    for pid in (ctx["p1"], ctx["p3"]):
        await _ejecutar(
            "update pedido set estado = 'pendiente', orden_entrega = null, latitud = :lat, longitud = :lon where id = :id",
            id=pid,
            lat=CLIENTE_CON_COORDS["latitud"],
            lon=CLIENTE_CON_COORDS["longitud"],
        )
    await _ejecutar(
        "update pedido set estado = 'pendiente', orden_entrega = null, latitud = null, longitud = null where id = :id",
        id=ctx["p2"],
    )


# --------------------------------------------------------------------------
# n8n simulado y cliente HTTP del panel
# --------------------------------------------------------------------------


class N8nFalso:
    """Reemplaza el webhook de n8n. `responder` recibe el payload enviado y
    devuelve un httpx.Response (o lanza una excepción de httpx)."""

    def __init__(self):
        self.llamadas: list[dict] = []
        self.responder = None

    def cliente(self) -> httpx.AsyncClient:
        async def manejar(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.llamadas.append({"headers": dict(request.headers), "payload": payload, "url": str(request.url)})
            resultado = self.responder(payload)
            if asyncio.iscoroutine(resultado):
                resultado = await resultado
            return resultado

        return httpx.AsyncClient(transport=httpx.MockTransport(manejar))


def _json(cuerpo: dict, codigo: int = 200) -> httpx.Response:
    return httpx.Response(codigo, json=cuerpo)


def _ruta_completa(payload: dict, sin_resolver: set[int] = frozenset()) -> httpx.Response:
    """Respuesta válida: todas las paradas en orden inverso al enviado (para
    verificar que se respeta el orden de n8n), salvo las de sin_resolver."""
    paradas = [p for p in payload["paradas"] if p["pedido_id"] not in sin_resolver]
    ruta = [
        {"pedido_id": p["pedido_id"], "orden_entrega": i, "latitud": -33.05 - i / 1000, "longitud": -71.6 - i / 1000}
        for i, p in enumerate(reversed(paradas), start=1)
    ]
    return _json(
        {
            "ruta": ruta,
            "sin_resolver": [{"pedido_id": pid, "motivo": "No se pudo geocodificar"} for pid in sin_resolver],
        }
    )


class Verificador:
    def __init__(self):
        self.errores = []

    def check(self, condicion: bool, descripcion: str) -> None:
        print(f"    [{'ok' if condicion else 'FALLA'}] {descripcion}")
        if not condicion:
            self.errores.append(descripcion)


async def _planificar(ctx: dict, ids: list[int]) -> httpx.Response:
    respuesta = await ctx["http"].post("/rutas/planificar", json={"pedido_ids": ids})
    print(f"  POST /rutas/planificar {ids} -> {respuesta.status_code}: {respuesta.text[:400]}")
    return respuesta


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------


async def caso_a(ctx: dict, v: Verificador) -> None:
    n8n = ctx["n8n"]
    n8n.responder = _ruta_completa
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    respuesta = await _planificar(ctx, ids)
    v.check(respuesta.status_code == 200, "200")
    llamada = n8n.llamadas[-1]
    v.check(llamada["url"] == URL_N8N_PRUEBA, "llama al webhook configurado")
    v.check(llamada["headers"].get("x-route-secret") == SECRETO_N8N_PRUEBA, "con el header X-Route-Secret")
    paradas = {p["pedido_id"]: p for p in llamada["payload"]["paradas"]}
    v.check(
        set(paradas) == set(ids)
        and paradas[ctx["p2"]]["latitud"] is None
        and paradas[ctx["p1"]]["direccion_texto"] == CLIENTE_CON_COORDS["direccion"]
        and all(set(p) == {"pedido_id", "direccion_texto", "latitud", "longitud"} for p in paradas.values()),
        "payload: paradas desde la BD, sin depósito ni datos extra",
    )
    cuerpo = respuesta.json()
    v.check([p["orden_entrega"] for p in cuerpo["ruta"]] == [1, 2, 3], "respuesta ordenada por orden_entrega")
    v.check([p["pedido_id"] for p in cuerpo["ruta"]] == list(reversed(ids)), "respeta el orden que dio n8n")
    pedidos = {pid: await _pedido(pid) for pid in ids}
    v.check(all(p["estado"] == "confirmado" for p in pedidos.values()), "todos pasan a confirmado")
    v.check(
        [pedidos[pid]["orden_entrega"] for pid in reversed(ids)] == [1, 2, 3], "cada uno con su orden_entrega"
    )
    v.check(
        pedidos[ctx["p2"]]["latitud"] is not None and float(pedidos[ctx["p2"]]["latitud"]) < -33.0,
        "el pedido sin coordenadas guarda las geocodificadas por n8n",
    )
    v.check(
        (pedidos[ctx["p1"]]["latitud"], pedidos[ctx["p1"]]["longitud"])
        == (CLIENTE_CON_COORDS["latitud"], CLIENTE_CON_COORDS["longitud"]),
        "el pedido que ya tenía coordenadas no se sobrescribe",
    )
    auditorias = await _ejecutar(
        "select count(*) from auditoria where entidad = 'pedido' and accion = 'planificar_ruta' "
        "and usuario = :usuario and entidad_id = any(:ids)",
        usuario=USUARIO_PRUEBA,
        ids=ids,
    )
    v.check(auditorias.scalar_one() == 3, "auditoría 'planificar_ruta' con el usuario del JWT, una por pedido")


async def caso_b(ctx: dict, v: Verificador) -> None:
    n8n = ctx["n8n"]
    n8n.responder = lambda payload: _ruta_completa(payload, sin_resolver={ctx["p2"]})
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    cuerpo = respuesta.json()
    v.check(respuesta.status_code == 200, "200")
    v.check(
        [p["pedido_id"] for p in cuerpo["sin_resolver"]] == [ctx["p2"]]
        and cuerpo["sin_resolver"][0]["motivo"] == "No se pudo geocodificar",
        "sin_resolver al final, con su motivo",
    )
    p2 = await _pedido(ctx["p2"])
    v.check(p2["estado"] == "confirmado" and p2["orden_entrega"] is None, "el sin_resolver queda confirmado con orden null")
    v.check(p2["latitud"] is None, "y sin coordenadas inventadas")
    otros = [await _pedido(pid) for pid in (ctx["p1"], ctx["p3"])]
    v.check(
        all(p["estado"] == "confirmado" and p["orden_entrega"] in (1, 2) for p in otros),
        "los demás confirmados con su orden",
    )


async def caso_c(ctx: dict, v: Verificador) -> None:
    antes = await _pedido(ctx["fuera"])
    ctx["n8n"].responder = _ruta_completa
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"]])
    despues = await _pedido(ctx["fuera"])
    v.check(respuesta.status_code == 200, "200")
    v.check(antes == despues, "el pedido fuera de la solicitud queda exactamente igual (estado, orden, fechas)")
    v.check((await _pedido(ctx["p3"]))["estado"] == "pendiente", "el pendiente no incluido sigue pendiente")
    v.check(
        {"pedido_id": ctx["fuera"], "orden_entrega": 7} in respuesta.json()["confirmados_con_orden_fuera_de_solicitud"],
        "se informa el confirmado con orden viejo que quedó fuera",
    )


async def _sin_cambios(ctx: dict, v: Verificador) -> None:
    for pid in (ctx["p1"], ctx["p2"], ctx["p3"]):
        pedido = await _pedido(pid)
        v.check(
            pedido["estado"] == "pendiente" and pedido["orden_entrega"] is None,
            f"pedido {pid} sigue pendiente, sin orden",
        )
    v.check((await _pedido(ctx["p2"]))["latitud"] is None, "sin coordenadas escritas")


async def caso_d(ctx: dict, v: Verificador) -> None:
    def timeout(payload):
        raise httpx.ReadTimeout("simulado")

    ctx["n8n"].responder = timeout
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(respuesta.status_code == 504 and "no respondió a tiempo" in respuesta.json()["detail"]["mensaje"], "timeout: 504 con mensaje claro")
    await _sin_cambios(ctx, v)

    ctx["n8n"].responder = lambda payload: _json({"error": "boom"}, 500)
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(respuesta.status_code == 502, "error 500 de n8n: 502")
    ctx["n8n"].responder = lambda payload: httpx.Response(200, text="<html>no json</html>")
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(respuesta.status_code == 502, "respuesta que no es JSON: 502")
    await _sin_cambios(ctx, v)


async def caso_e(ctx: dict, v: Verificador) -> None:
    def ajeno(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        cuerpo["ruta"][0]["pedido_id"] = 999999999
        return _json(cuerpo)

    def orden_duplicado(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        cuerpo["ruta"][1]["orden_entrega"] = cuerpo["ruta"][0]["orden_entrega"]
        return _json(cuerpo)

    def en_ambos(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        cuerpo["sin_resolver"].append({"pedido_id": cuerpo["ruta"][0]["pedido_id"], "motivo": "x"})
        return _json(cuerpo)

    def latitud_invalida(payload):
        cuerpo = json.loads(_ruta_completa(payload).content)
        cuerpo["ruta"][0]["latitud"] = 123.0
        return _json(cuerpo)

    for nombre, responder in (
        ("pedido_id ajeno", ajeno),
        ("orden_entrega duplicado", orden_duplicado),
        ("pedido en ruta y sin_resolver", en_ambos),
        ("latitud fuera de rango", latitud_invalida),
    ):
        ctx["n8n"].responder = responder
        respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
        v.check(respuesta.status_code == 502 and respuesta.json()["detail"].get("problemas"), f"{nombre}: 502 con el problema")
    await _sin_cambios(ctx, v)


async def caso_f(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    llamadas = len(ctx["n8n"].llamadas)
    ids = [ctx["p1"], ctx["cancelado"], ctx["entregado"], 999999999]
    respuesta = await _planificar(ctx, ids)
    detalle = respuesta.json()["detail"]
    v.check(respuesta.status_code == 409, "409")
    v.check(
        sorted(detalle["pedido_ids"]) == sorted([ctx["cancelado"], ctx["entregado"], 999999999]),
        "con los IDs problemáticos (cancelado, entregado e inexistente)",
    )
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")
    await _sin_cambios(ctx, v)


async def caso_g(ctx: dict, v: Verificador) -> None:
    llamadas = len(ctx["n8n"].llamadas)
    vacia = await _planificar(ctx, [])
    repetidos = await _planificar(ctx, [ctx["p1"], ctx["p1"]])
    v.check(vacia.status_code == 422, "lista vacía: 422")
    v.check(repetidos.status_code == 422 and repetidos.json()["detail"]["pedido_ids"] == [ctx["p1"]], "IDs repetidos: 422 con el ID")
    v.check(len(ctx["n8n"].llamadas) == llamadas, "sin llamar a n8n")


async def caso_h(ctx: dict, v: Verificador) -> None:
    liberar = asyncio.Event()

    async def lento(payload):
        await liberar.wait()
        return _ruta_completa(payload)

    ctx["n8n"].responder = lento
    llamadas = len(ctx["n8n"].llamadas)
    ids = [ctx["p1"], ctx["p2"], ctx["p3"]]
    primera = asyncio.create_task(_planificar(ctx, ids))
    await asyncio.sleep(0.5)  # la primera ya está esperando a n8n
    segunda = await _planificar(ctx, ids)
    liberar.set()
    primera = await primera
    v.check(segunda.status_code == 409, "la segunda llamada simultánea recibe 409")
    v.check(primera.status_code == 200, "la primera termina bien")
    v.check(len(ctx["n8n"].llamadas) == llamadas + 1, "n8n se llama una sola vez")


async def caso_i(ctx: dict, v: Verificador) -> None:
    v.check(await _cliente(ctx["c1"]) == ctx["cliente_antes"][ctx["c1"]], "cliente con coordenadas: dirección y coordenadas intactas")
    v.check(await _cliente(ctx["c2"]) == ctx["cliente_antes"][ctx["c2"]], "cliente sin coordenadas: sigue sin coordenadas (no se le copian las del pedido)")


async def caso_j(ctx: dict, v: Verificador) -> None:
    ctx["n8n"].responder = _ruta_completa
    await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    ctx["n8n"].responder = lambda payload: _ruta_completa(
        {"paradas": list(reversed(payload["paradas"]))}
    )
    respuesta = await _planificar(ctx, [ctx["p1"], ctx["p2"], ctx["p3"]])
    v.check(respuesta.status_code == 200, "regenerar con pedidos ya confirmados: 200")
    v.check(
        [(await _pedido(pid))["orden_entrega"] for pid in (ctx["p1"], ctx["p2"], ctx["p3"])] == [1, 2, 3],
        "con el orden nuevo",
    )


async def caso_k(ctx: dict, v: Verificador) -> None:
    respuesta = await ctx["http"].get("/rutas/pedidos-pendientes")
    filas = respuesta.json()
    ids = {f["pedido_id"] for f in filas}
    estados = await _ejecutar("select distinct estado::text from pedido where id = any(:ids)", ids=list(ids))
    v.check(respuesta.status_code == 200, "200")
    v.check([e[0] for e in estados] == ["pendiente"], "solo pedidos pendientes")
    v.check({ctx["p1"], ctx["p2"], ctx["p3"]} <= ids, "incluye los pendientes de prueba")
    v.check(not ids & {ctx["fuera"], ctx["cancelado"], ctx["entregado"]}, "excluye confirmados, cancelados y entregados")
    fila_p2 = next(f for f in filas if f["pedido_id"] == ctx["p2"])
    v.check(
        fila_p2["cliente_nombre"] == CLIENTE_SIN_COORDS["nombre"]
        and fila_p2["direccion_texto"] == CLIENTE_SIN_COORDS["direccion"]
        and fila_p2["latitud"] is None
        and fila_p2["creado_en"],
        "con cliente, dirección, coordenadas (null si no tiene) y fecha de creación",
    )


async def caso_l(ctx: dict, v: Verificador) -> None:
    url = settings.N8N_ROUTE_WEBHOOK_URL
    settings.N8N_ROUTE_WEBHOOK_URL = ""
    try:
        respuesta = await _planificar(ctx, [ctx["p1"]])
    finally:
        settings.N8N_ROUTE_WEBHOOK_URL = url
    v.check(respuesta.status_code == 503 and "N8N_ROUTE_WEBHOOK_URL" in respuesta.json()["detail"]["mensaje"], "sin URL configurada: 503 claro (no 500)")
    await _sin_cambios(ctx, v)

    del app.dependency_overrides[get_current_user]
    try:
        sin_token = await ctx["http"].post("/rutas/planificar", json={"pedido_ids": [ctx["p1"]]})
        sin_token_get = await ctx["http"].get("/rutas/pedidos-pendientes")
    finally:
        app.dependency_overrides[get_current_user] = _usuario_prueba
    v.check(sin_token.status_code == 401 and sin_token_get.status_code == 401, "sin JWT: 401 en ambos endpoints")


def _usuario_prueba() -> dict:
    return {"user_id": "test", "email": USUARIO_PRUEBA}


CASOS = [
    ("a", "Éxito con todos resueltos: confirmados con su orden", caso_a),
    ("b", "Éxito con algunos sin_resolver: confirmados, sin_resolver con orden null", caso_b),
    ("c", "Pedidos fuera de la solicitud no cambian (y se informan)", caso_c),
    ("d", "Timeout o error de n8n: nada se persiste", caso_d),
    ("e", "Respuesta inválida de n8n: nada se persiste", caso_e),
    ("f", "Cancelado, entregado o inexistente: 409 con los IDs, sin llamar a n8n", caso_f),
    ("g", "Lista vacía o IDs repetidos: error de validación", caso_g),
    ("h", "Segunda llamada simultánea: 409", caso_h),
    ("i", "Dirección y coordenadas del cliente nunca cambian", caso_i),
    ("j", "Regenerar la ruta con pedidos ya confirmados", caso_j),
    ("k", "GET /rutas/pedidos-pendientes devuelve solo pendientes", caso_k),
    ("l", "Sin URL de n8n: 503; sin JWT: 401", caso_l),
]


async def main() -> None:
    await _limpiar()
    ctx = {}
    ctx["c1"] = await _crear_cliente(CLIENTE_CON_COORDS)
    ctx["c2"] = await _crear_cliente(CLIENTE_SIN_COORDS)
    ctx["p1"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "pendiente")
    ctx["p2"] = await _crear_pedido(CLIENTE_SIN_COORDS, ctx["c2"], "pendiente", con_coords=False)
    ctx["p3"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "pendiente")
    ctx["fuera"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "confirmado", orden=7)
    ctx["cancelado"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "cancelado")
    ctx["entregado"] = await _crear_pedido(CLIENTE_CON_COORDS, ctx["c1"], "entregado")
    ctx["cliente_antes"] = {cid: await _cliente(cid) for cid in (ctx["c1"], ctx["c2"])}
    print(f"Datos de prueba: {ctx}")

    original = (settings.N8N_ROUTE_WEBHOOK_URL, settings.N8N_ROUTE_WEBHOOK_SECRET, ruta_service._cliente_http)
    ctx["n8n"] = N8nFalso()
    settings.N8N_ROUTE_WEBHOOK_URL = URL_N8N_PRUEBA
    settings.N8N_ROUTE_WEBHOOK_SECRET = SECRETO_N8N_PRUEBA
    ruta_service._cliente_http = ctx["n8n"].cliente
    app.dependency_overrides[get_current_user] = _usuario_prueba

    resultados = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            ctx["http"] = http
            for letra, descripcion, caso in CASOS:
                print("#" * 70)
                print(f"Caso {letra}: {descripcion}")
                print("#" * 70)
                await _reiniciar(ctx)
                v = Verificador()
                try:
                    await caso(ctx, v)
                except Exception as exc:
                    print(f"    [ERROR] {exc!r}")
                    v.errores.append(repr(exc))
                resultados.append((letra, descripcion, not v.errores))
                print()
    finally:
        settings.N8N_ROUTE_WEBHOOK_URL, settings.N8N_ROUTE_WEBHOOK_SECRET, ruta_service._cliente_http = original
        app.dependency_overrides.pop(get_current_user, None)
        await _limpiar()

    print("=" * 70)
    print("Resumen final:")
    for letra, descripcion, ok in resultados:
        print(f"  [{'OK' if ok else 'FALLO'}] {letra}. {descripcion}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
