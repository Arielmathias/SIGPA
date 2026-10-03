"""Orquestación del flujo de pedidos por WhatsApp.

Conecta interpret_message (interpretación del LLM), construir_resumen_pedido
(precios reales desde la BD) y draft_store (borrador de pedido en curso por
cliente) para decidir qué responder en cada mensaje entrante.

Reparto de responsabilidades:
- El LLM solo EXTRAE datos del mensaje (productos, aclaraciones, nombre,
  dirección, rechazo de ubicación, etc.) y redacta respuestas para consultas
  de precio/pedidos o preguntas sobre productos.
- El CÓDIGO decide qué dato falta y qué se le pregunta al cliente en cada
  turno (ver _datos_faltantes), de a un paso, según el draft y si el cliente
  ya existe en la tabla cliente (identificado por teléfono, ver
  cliente_lookup.py). Así reglas como "nunca pedir el nombre a un cliente
  existente" o "nunca preguntar '¿algo más?' sin productos" no dependen del
  prompt.
- La tabla cliente solo se escribe al confirmar el pedido, en la misma
  transacción que crea el pedido (ver _confirmar_pedido).
"""

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Auditoria, Cliente, DetallePedido, Pedido, Producto
from app.models.enums import EstadoPedido
from app.services.agent_service import (
    CATALOGO_NOMBRES,
    _formatear_clp,
    construir_resumen_pedido,
    interpret_message,
)
from app.services.auditoria_service import construir_snapshot
from app.services.cliente_lookup import buscar_clientes_por_telefono, normalizar_telefono
from app.services.conversacion_bot_service import marcar_inactiva
from app.services.draft_store import clear_draft, get_draft, get_lock, save_draft
from app.services.whatsapp_client import send_whatsapp_message

logger = logging.getLogger(__name__)

# Estados del draft una vez que ya se mostró el resumen. Solo desde
# "esperando_confirmacion" (el último mensaje del bot fue el resumen) un sí
# explícito confirma el pedido; tras un "no" se pasa a
# "esperando_modificacion", donde un "sí" NO confirma (ver procesar_mensaje).
ESTADO_ESPERANDO_CONFIRMACION = "esperando_confirmacion"
ESTADO_ESPERANDO_MODIFICACION = "esperando_modificacion"

# Únicas respuestas que confirman el pedido (texto normalizado, sin tildes).
# Pueden ir acompañadas de cortesía ("sí, gracias"), nada más: "ok", "ya" o
# "bueno" no confirman.
CONFIRMACIONES = {"si", "confirmo", "confirmar", "dale"}
_CORTESIA = {"por", "favor", "porfa", "gracias"}

# Negativa "pura" ante el resumen ("no", "no gracias", "todavía no"). Un "no"
# con más contenido ("no, agrega un bidón") pasa al LLM como modificación.
_NEGACIONES_SIMPLES = {"no", "nop", "nope", "nel", "negativo"}
_PALABRAS_NEGATIVA = _NEGACIONES_SIMPLES | {
    "gracias", "todavia", "aun", "mejor", "por", "ahora", "asi", "lo", "confirmo", "quiero",
}

# Valor de auditoria.usuario para los cambios en cliente hechos por el bot
# (los endpoints del panel usan el email del usuario autenticado).
USUARIO_AUDITORIA_BOT = "bot_whatsapp"

PREGUNTA_PRODUCTO = (
    "¿Qué producto y qué cantidad quieres pedir? Tenemos bidones de 12L y 20L "
    "(nuevos o de recarga), dispensadores y promociones."
)

PREGUNTA_ALGO_MAS = "¿Deseas agregar algo más a tu pedido?"

PREGUNTA_QUE_MAS = "¡Claro! ¿Qué otro producto y qué cantidad quieres agregar?"

MENSAJE_PEDIR_NOMBRE = "¿A nombre de quién registramos tu pedido?"

PREGUNTA_CONFIRMAR_DIRECCION = "¿Despachamos a {direccion}?"

PREGUNTA_DIRECCION_Y_UBICACION = (
    "¿Cuál es la dirección de despacho (calle y número)? Si puedes, compártela "
    "también como ubicación de WhatsApp: usa el clip 📎 y selecciona 'Ubicación'."
)

PREGUNTA_DIRECCION_NUEVA_Y_UBICACION = (
    "Entendido. ¿Cuál es la nueva dirección de despacho (calle y número)? Si puedes, "
    "compártela también como ubicación de WhatsApp: usa el clip 📎 y selecciona 'Ubicación'."
)

# Sin mencionar la ubicación de WhatsApp: se usa cuando la ubicación ya se
# recibió o el cliente ya la rechazó (camino de solo texto).
PREGUNTA_DIRECCION_TEXTO = "¿Me escribes la dirección de despacho (calle y número)?"

PREGUNTA_UBICACION = (
    "¿Me compartes tu ubicación de WhatsApp? Usa el clip 📎 y selecciona 'Ubicación'. "
    "Si no puedes, avísame y seguimos solo con la dirección que me diste."
)

PREGUNTA_CAPACIDAD_PROMO = "¿Prefieres que los bidones de la promo sean de 12L o de 20L?"

PREGUNTA_ACLARACION_BIDON = (
    "¿{cantidad} bidón(es) de {capacidad}L nuevo(s) (con envase) o de recarga "
    "(solo el agua, entregando tu bidón vacío)?"
)

MENSAJE_UBICACION_RECIBIDA = "¡Gracias, recibí tu ubicación!"

MENSAJE_ERROR_PEDIDO = (
    "Hubo un problema al registrar tu pedido, por favor intenta de nuevo o contacta a un ejecutivo."
)

MENSAJE_PEDIDO_CANCELADO = (
    "Listo, cancelé tu pedido en curso. Si quieres hacer un pedido nuevo, cuéntame qué necesitas."
)

MENSAJE_NO_CONFIRMADO = "Entendido, no lo confirmo. ¿Qué quieres cambiar o prefieres cancelar el pedido?"

PREGUNTA_CANCELAR = "¿Quieres cancelar el pedido? Responde CANCELAR, o dime qué quieres cambiar."

PREFIJO_REPETIR_RESUMEN = "Antes de confirmar, revisemos tu pedido una vez más."

MENSAJE_PRODUCTOS_NO_REGISTRADOS = (
    "Antes de mostrarte el resumen, revisemos tu pedido: tengo anotado {registrados}, "
    "pero también mencionaste {faltantes}, que no quedó registrado. ¿Qué quieres agregar? "
    "Si no quieres agregar nada, responde NO."
)

MENSAJE_CLIENTE_DUPLICADO = (
    "¡Gracias por escribirnos! Una ejecutiva revisará tus datos y te contactará "
    "a la brevedad para completar tu pedido."
)

MENSAJE_NOTIFICACION_DUPLICADO = (
    "Hola, el teléfono {telefono} escribió al WhatsApp de pedidos, pero está registrado "
    "en más de un cliente (ids {ids}). El bot no tomó el pedido: por favor revisa la "
    "ficha y contacta al cliente."
)

# Intenciones cuya "respuesta_sugerida" se envía tal cual (la arma el backend
# desde la BD o es el rechazo de un tema fuera de alcance), sin reemplazarla
# por la pregunta del paso pendiente.
_INTENCIONES_RESPUESTA_LLM = ("consulta_precio", "consulta_pedidos", "fuera_de_alcance")

# Palabras que indican una cancelación EXPLÍCITA del pedido en curso. Es la
# única forma de vaciar un draft con productos ya confirmados (ver
# _aplicar_resultado_llm): un simple saludo o mensaje ambiguo nunca debe
# borrar el pedido, pero esto sí. Se comparan con similitud difusa para
# tolerar erratas ("canelar", "cancelr"), ver _intencion_cancelar.
_PALABRAS_CANCELACION = (
    "cancela", "cancelar", "cancelo", "cancele", "cancelen", "cancelalo", "cancelarlo",
    "anula", "anular", "anulo", "anulalo", "anularlo",
)

# Umbrales de SequenceMatcher.ratio() contra _PALABRAS_CANCELACION, medidos
# con erratas y palabras parecidas: una errata de una letra ("canelar",
# "cacelar", "cancelr", "cancear") da 0,93; "canela"/"canelo" 0,92;
# "canelos"/"candela" 0,86; "manuela"/"ancla" 0,83; "cancha" 0,77;
# "celular"/"calle" 0,67.
UMBRAL_CANCELACION = 0.93
UMBRAL_DUDA_CANCELACION = 0.8

# Una errata solo cancela directamente en mensajes cortos ("canelar",
# "canelar el pedido"); en uno más largo se pregunta.
MAX_PALABRAS_CANCELACION_DIFUSA = 3

# Pasos en que el cliente escribe texto libre (nombres, calles): ahí la
# similitud difusa nunca cancela por sí sola ("Los Canelos 345", "Candela").
_PASOS_TEXTO_LIBRE = ("nombre", "direccion", "ubicacion", "confirmar_direccion")

# Negaciones que, antes de la palabra de cancelación y en la misma frase,
# indican que el cliente NO quiere cancelar ("no cancelen mi pedido").
_NEGACIONES = {"no", "nunca", "ni", "tampoco", "jamas"}

# Palabras de cada tipo de producto, para saber si un mensaje menciona
# productos (texto normalizado, ver _normalizar_texto).
_PATRONES_TIPO_PRODUCTO = {
    "bidon": re.compile(r"\b(bidon\w*|recargas?|nuev[oa]s?|litros?|envases?|12|20|12l|20l)\b"),
    "dispensador": re.compile(r"\b(dispensador\w*|usb|basico\w*|maquina\w*)\b"),
    "promo": re.compile(r"\b(promo\w*|combo\w*)\b"),
}

# Pedido EXPLÍCITO de cambiar o quitar algo ya pedido ("cambia", "mejor
# dos", "quita el dispensador"). Sin esto, una línea del pedido nunca se
# reemplaza ni se elimina: lo que extrae el LLM solo se suma.
_PATRON_MODIFICACION = re.compile(
    r"\b(cambi\w*|mejor|quit\w*|saca\w*|elimin\w*|borr\w*|reemplaz\w*|correg\w*|corrig\w*"
    r"|solo|sin|deja\w*|sean|en vez|en lugar|ya no|no quiero)\b"
)

_NUMEROS_TEXTO = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}

# Palabras que pueden ir entre una cantidad y la variante del bidón
# ("2 bidones de 20 recarga").
_RELLENO_CANTIDAD_VARIANTE = {"bidon", "bidones", "de", "l", "litro", "litros"}

# Frases (no solo palabras sueltas) que indican que el texto está PIDIENDO o
# volviendo a CONFIRMAR dirección/ubicación al cliente. Se usan para no dejar
# pasar una respuesta del LLM que salta a otro paso cuando lo pendiente es
# el producto (ver _respuesta_es_de_producto).
_FRASES_REABREN_DIRECCION_UBICACION = (
    "confirmas tu dirección",
    "confirmas tu direccion",
    "confirmes tu dirección",
    "confirmes tu direccion",
    "confirmar tu dirección",
    "confirmar tu direccion",
    "indicar una distinta",
    "indicar tu dirección",
    "indicar tu direccion",
    "indica tu dirección",
    "indica tu direccion",
    "cuál es tu dirección",
    "cual es tu direccion",
    "dirección de despacho",
    "direccion de despacho",
    "compartas tu ubicación",
    "compartas tu ubicacion",
    "compartas la ubicación",
    "compartas la ubicacion",
    "compartir tu ubicación",
    "compartir tu ubicacion",
    "comparte tu ubicación",
    "comparte tu ubicacion",
    "necesito tu ubicación",
    "necesito tu ubicacion",
    "necesito que compartas",
)

_PALABRAS_PRODUCTO = (
    "bidón",
    "bidon",
    "dispensador",
    "promo",
    "recarga",
    "nuevo",
    "12l",
    "20l",
    "12 l",
    "20 l",
    "litros",
    "producto",
)

_AFIRMATIVAS = (
    "si",
    "dale",
    "ok",
    "okay",
    "correcto",
    "confirmo",
    "exacto",
    "claro",
    "perfecto",
    "ya",
    "bueno",
    "esa",
    "esa misma",
    "la misma",
    "la de siempre",
    "de acuerdo",
    "afirmativo",
)

_INDICADORES_DIRECCION_DISTINTA = ("otra", "distinta", "diferente", "cambi", "nueva")

_INDICADORES_NADA_MAS = (
    "nada mas",
    "eso es todo",
    "es todo",
    "solo eso",
    "ya esta",
    "eso seria",
    "eso nomas",
    "nada",
)

_PREFIJOS_NOMBRE = ("a nombre de", "mi nombre es", "me llamo", "soy")


def _normalizar_texto(texto: str | None) -> str:
    """Minúsculas, sin tildes ni puntuación, espacios colapsados."""
    texto = unicodedata.normalize("NFKD", (texto or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^\w\s]", " ", texto)
    return " ".join(texto.split())


def _empieza_con(texto_normalizado: str, frases: tuple[str, ...]) -> bool:
    return any(
        texto_normalizado == frase or texto_normalizado.startswith(frase + " ")
        for frase in frases
    )


def _es_afirmativa(texto: str | None) -> bool:
    return _empieza_con(_normalizar_texto(texto), _AFIRMATIVAS)


def _pide_direccion_distinta(texto: str | None) -> bool:
    """Respuesta negativa a "¿Despachamos a {dirección}?" ("no", "no, es
    otra", "quiero cambiarla", etc.)."""
    texto_normalizado = _normalizar_texto(texto)
    return _empieza_con(texto_normalizado, ("no",)) or any(
        indicador in texto_normalizado for indicador in _INDICADORES_DIRECCION_DISTINTA
    )


def _no_quiere_nada_mas(texto: str | None) -> bool:
    """Respuesta negativa a "¿Deseas agregar algo más?"."""
    texto_normalizado = _normalizar_texto(texto)
    return _empieza_con(texto_normalizado, ("no",)) or any(
        indicador in texto_normalizado for indicador in _INDICADORES_NADA_MAS
    )


def _nombre_desde_texto(texto: str | None) -> str | None:
    """Fallback en código para el paso "nombre": si el LLM no extrajo el
    nombre pero el mensaje es claramente solo un nombre de persona (pocas
    palabras, sin dígitos), se toma tal cual."""
    nombre = (texto or "").strip().strip(".!¡")
    nombre_normalizado = _normalizar_texto(nombre)
    for prefijo in _PREFIJOS_NOMBRE:
        if nombre_normalizado.startswith(prefijo + " "):
            nombre = " ".join(nombre.split()[len(prefijo.split()):])
            break
    palabras = nombre.split()
    if not 1 <= len(palabras) <= 4 or any(ch.isdigit() for ch in nombre):
        return None
    if not all(palabra.replace("-", "").replace("'", "").isalpha() for palabra in palabras):
        return None
    return nombre


def _es_confirmacion_explicita(texto: str | None) -> bool:
    tokens = _normalizar_texto(texto).split()
    return any(t in CONFIRMACIONES for t in tokens) and all(
        t in CONFIRMACIONES or t in _CORTESIA for t in tokens
    )


def _es_negativa_simple(texto: str | None) -> bool:
    tokens = _normalizar_texto(texto).split()
    return any(t in _NEGACIONES_SIMPLES for t in tokens) and all(
        t in _PALABRAS_NEGATIVA for t in tokens
    )


def _tipo_producto(nombre: str) -> str:
    if nombre.startswith("Promo"):
        return "promo"
    if nombre.startswith("Dispensador"):
        return "dispensador"
    return "bidon"


def _menciona_tipo(tipo: str, texto_normalizado: str) -> bool:
    return bool(_PATRONES_TIPO_PRODUCTO[tipo].search(texto_normalizado))


def _menciona_algun_producto(texto_normalizado: str) -> bool:
    return any(_menciona_tipo(tipo, texto_normalizado) for tipo in _PATRONES_TIPO_PRODUCTO)


def _intencion_cancelar(texto: str | None, texto_libre: bool = False) -> str | None:
    """"cancelar" si el mensaje cancela el pedido en curso, "duda" si podría
    querer cancelar pero no es claro (hay que preguntarle), None si no.

    Detección por palabra clave, tolerante a erratas ("canelar"). RIESGO de
    falsos positivos: una palabra parecida a "cancelar" no siempre significa
    "cancela el pedido", y cancelar borra todo el draft. Por eso:
    - Frases con una negación hasta 3 palabras antes, en la misma frase, NO
      cancelan: "no cancelen mi pedido", "no me lo cancelen". La frase se
      corta en la puntuación, así que "no, cancela" sí cancela.
    - Si la frase menciona productos ("cancela el dispensador") puede querer
      quitar solo ese producto: es "duda", no se cancela todo.
    - Solo cancelan directamente las formas exactas (empiezan con "cancel" o
      "anul") y las erratas de una letra en mensajes cortos. Una similitud
      menor ("canelos", "candela", "manuela") o una errata dentro de un
      mensaje largo es "duda".
    - En pasos de texto libre (nombre, dirección) la similitud difusa nunca
      cancela, y solo pregunta si el mensaje es una sola palabra con una
      errata de una letra ("canelar"): "Los Canelos 345" es una dirección y
      "Manuela" un nombre, no cancelaciones.
    - Palabras de menos de 5 letras no se evalúan ("nulo").
    Si aparece un falso positivo nuevo, conviene sumar la palabra a una
    lista de excepciones en vez de subir los umbrales.
    """
    frases = re.split(r"[,.;:!?¿¡\n]+", texto or "")
    total_palabras = len(_normalizar_texto(texto).split())
    resultado = None
    for frase in frases:
        tokens = _normalizar_texto(frase).split()
        for i, token in enumerate(tokens):
            if len(token) < 5:
                continue
            exacta = token.startswith(("cancel", "anul"))
            similitud = 1.0 if exacta else max(
                SequenceMatcher(None, token, palabra).ratio() for palabra in _PALABRAS_CANCELACION
            )
            if similitud < UMBRAL_DUDA_CANCELACION:
                continue
            if any(t in _NEGACIONES for t in tokens[max(0, i - 3):i]):
                continue
            if texto_libre and not exacta and (
                total_palabras > 1 or similitud < UMBRAL_CANCELACION
            ):
                continue
            menciona_producto = _menciona_algun_producto(" ".join(tokens))
            if exacta and not menciona_producto:
                return "cancelar"
            if (
                similitud >= UMBRAL_CANCELACION
                and not texto_libre
                and not menciona_producto
                and total_palabras <= MAX_PALABRAS_CANCELACION_DIFUSA
            ):
                return "cancelar"
            resultado = "duda"
    return resultado


def _cantidad_valida(valor) -> int:
    """Cantidad entera de un ítem; sin cantidad es 1 (regla del prompt)."""
    if valor is None:
        return 1
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def _categoria(nombre: str) -> str:
    """Agrupa las variantes de un mismo bidón ("Bidón 20L Recarga" y "Bidón
    20L Nuevo" son "Bidón 20L"); el resto de productos es su propio nombre."""
    coincidencia = re.match(r"Bidón (12|20)L\b", nombre)
    return f"Bidón {coincidencia.group(1)}L" if coincidencia else nombre


def _unidades_por_categoria(productos: list[dict], aclaracion: dict | None) -> dict[str, int]:
    """Unidades por categoría del draft, contando también los bidones que
    esperan la aclaración "nuevo o recarga"."""
    unidades: dict[str, int] = {}
    for item in productos:
        categoria = _categoria(item.get("nombre_producto") or "")
        unidades[categoria] = unidades.get(categoria, 0) + _cantidad_valida(item.get("cantidad"))
    if aclaracion and aclaracion.get("capacidad_litros") in (12, 20):
        categoria = f"Bidón {aclaracion['capacidad_litros']}L"
        unidades[categoria] = unidades.get(categoria, 0) + _cantidad_valida(aclaracion.get("cantidad"))
    return unidades


def _sumar_linea(productos: list[dict], nombre: str, cantidad: int) -> None:
    for item in productos:
        if item.get("nombre_producto") == nombre:
            item["cantidad"] = _cantidad_valida(item.get("cantidad")) + cantidad
            return
    productos.append({"nombre_producto": nombre, "cantidad": cantidad})


def _fusionar_productos(
    previos: list[dict],
    extraidos: list[dict],
    mensaje: str | None,
    excluir_categorias: frozenset[str] = frozenset(),
) -> tuple[list[dict], set[str]]:
    """Fusiona lo que extrajo el LLM en este turno con los productos del
    draft. Devuelve (productos, categorías que el cliente cambió o quitó
    explícitamente).

    - "agregar" (por defecto) suma: misma línea (producto y variante) suma
      cantidad, producto distinto agrega una línea.
    - "fijar"/"quitar" solo se aplican si el mensaje pide explícitamente un
      cambio (_PATRON_MODIFICACION): nunca se reemplaza ni borra una línea
      porque el LLM devolvió una lista distinta (bug del 2026-10-03: "si, un
      dispensador usb" dejó el pedido solo con el dispensador).
    - Un ítem "agregar" de un tipo de producto que el mensaje no menciona se
      descarta: es el LLM repitiendo productos del contexto (ej. responde
      "sí" a la dirección y devuelve los bidones de nuevo), y sumarlo los
      duplicaría.
    - Nombres fuera del catálogo y cantidades < 1 se descartan.
    """
    texto = _normalizar_texto(mensaje)
    modificacion_explicita = bool(_PATRON_MODIFICACION.search(texto))
    productos = [dict(item) for item in previos]
    categorias_modificadas: set[str] = set()

    for item in extraidos:
        nombre = item.get("nombre_producto")
        if nombre not in CATALOGO_NOMBRES:
            logger.warning("[order_flow] Producto fuera del catálogo descartado: %r", nombre)
            continue
        if _categoria(nombre) in excluir_categorias:
            continue
        operacion = item.get("operacion") or "agregar"
        indice = next(
            (i for i, p in enumerate(productos) if p.get("nombre_producto") == nombre), None
        )

        if operacion in ("fijar", "quitar") and not modificacion_explicita:
            if operacion == "quitar" or indice is not None:
                logger.info("[order_flow] '%s' de %s ignorado: el cliente no pidió un cambio", operacion, nombre)
                continue
            operacion = "agregar"

        if operacion == "agregar" and not _menciona_tipo(_tipo_producto(nombre), texto):
            logger.info("[order_flow] %s descartado: el mensaje no menciona ese producto (eco del contexto)", nombre)
            continue

        if operacion == "quitar":
            if indice is None:
                # "quita el dispensador" cuando el LLM eligió el otro modelo:
                # si hay una sola línea de ese tipo, es esa.
                del_tipo = [
                    i for i, p in enumerate(productos)
                    if _tipo_producto(p.get("nombre_producto") or "") == _tipo_producto(nombre)
                ]
                indice = del_tipo[0] if len(del_tipo) == 1 else None
            if indice is not None:
                categorias_modificadas.add(_categoria(productos[indice]["nombre_producto"]))
                productos.pop(indice)
            continue

        cantidad = _cantidad_valida(item.get("cantidad"))
        if cantidad < 1:
            continue
        if operacion == "fijar":
            categorias_modificadas.add(_categoria(nombre))
            if indice is not None:
                productos[indice] = {**productos[indice], "cantidad": cantidad}
                continue
        _sumar_linea(productos, nombre, cantidad)

    return productos, categorias_modificadas


def _capacidad_en(tokens: list[str], j: int) -> int | None:
    """Capacidad si tokens[j] la indica: "12l"/"20l", o "12"/"20" después de
    "de" ("de 20 litros")."""
    coincidencia = re.fullmatch(r"(12|20)l", tokens[j])
    if coincidencia:
        return int(coincidencia.group(1))
    if tokens[j] in ("12", "20") and j > 0 and tokens[j - 1] == "de":
        return int(tokens[j])
    return None


def _mencion_bidon(tokens: list[str], indice: int) -> tuple[int | None, int | None]:
    """(cantidad, capacidad) escritas alrededor de la variante en
    tokens[indice]: "2 recargas", "dos nuevos", "2 bidones de 20 litros
    recarga", "1 de 12 nuevo", "2 recargas de 20". None si no aparecen."""
    cantidad = capacidad = None
    j = indice - 1
    while j >= 0:
        token = tokens[j]
        capacidad_token = _capacidad_en(tokens, j)
        if capacidad_token is not None:
            capacidad = capacidad or capacidad_token
        elif token.isdigit():
            cantidad = int(token)
            break
        elif token in _NUMEROS_TEXTO:
            cantidad = _NUMEROS_TEXTO[token]
            break
        elif token not in _RELLENO_CANTIDAD_VARIANTE:
            break
        j -= 1
    if capacidad is None and indice + 2 < len(tokens) and tokens[indice + 1] == "de":
        capacidad = _capacidad_en(tokens, indice + 2)
    if capacidad is None and indice + 1 < len(tokens):
        coincidencia = re.fullmatch(r"(12|20)l", tokens[indice + 1])
        capacidad = int(coincidencia.group(1)) if coincidencia else None
    return cantidad, capacidad


def _menciones_bidon(texto_normalizado: str) -> list[tuple[str, int | None, int | None]]:
    """[(variante, cantidad, capacidad)] de cada "recarga"/"nuevo" del
    mensaje, en orden."""
    tokens = texto_normalizado.split()
    menciones = []
    for i, token in enumerate(tokens):
        if re.fullmatch(r"recargas?", token):
            variante = "Recarga"
        elif re.fullmatch(r"nuev[oa]s?", token):
            variante = "Nuevo"
        else:
            continue
        menciones.append((variante, *_mencion_bidon(tokens, i)))
    return menciones


def _cantidades_por_variante(texto_normalizado: str, capacidad: int) -> dict[str, int | None]:
    """{"Recarga": n, "Nuevo": m} según lo que el mensaje dice de cada
    variante de esa capacidad (o sin capacidad explícita), en el orden en que
    se mencionan (None = mencionada sin cantidad)."""
    cantidades: dict[str, int | None] = {}
    for variante, cantidad, capacidad_mencion in _menciones_bidon(texto_normalizado):
        if capacidad_mencion not in (None, capacidad):
            continue
        if cantidades.get(variante) is not None and cantidad is not None:
            cantidades[variante] += cantidad
        elif cantidad is not None or variante not in cantidades:
            cantidades[variante] = cantidad
    return cantidades


def _resolver_aclaracion_bidon(
    aclaracion: dict | None, mensaje: str | None
) -> tuple[dict | None, list[dict]] | None:
    """Resuelve en código la pregunta "¿nuevo o recarga?" de un bidón, sin
    depender del LLM. Devuelve (aclaración que sigue pendiente o None, líneas
    a agregar), o None si el mensaje no la resuelve.

    - "recarga" / "los quiero nuevos": toda la cantidad pendiente a esa
      variante (cubre también el error del LLM con "Quiero 2 bidones de 20
      litros recarga", que dejaba el ítem pendiente pese al "recarga").
    - "2 recargas y 2 nuevos": una línea por variante.
    - "1 nuevo y el resto recarga": la variante sin número se lleva el resto.
    - "2 recargas" de 4 pendientes: quedan 2 pendientes y se vuelve a
      preguntar por ellos.
    - "¿nuevo o recarga?" (ambas sin número) no resuelve nada.
    - "1 de 12 nuevo" en el mismo mensaje es otro bidón, ya completo: se
      agrega como línea aparte.
    """
    if not aclaracion:
        return None
    capacidad = aclaracion.get("capacidad_litros")
    pendiente = _cantidad_valida(aclaracion.get("cantidad"))
    if capacidad not in (12, 20) or pendiente < 1:
        return None

    texto = _normalizar_texto(mensaje)
    cantidades = _cantidades_por_variante(texto, capacidad)
    sin_cantidad = [variante for variante, n in cantidades.items() if n is None]
    if not cantidades or len(sin_cantidad) > 1:
        return None
    if sin_cantidad:
        resto = pendiente - sum(n for n in cantidades.values() if n is not None)
        if resto < 1:
            return None
        cantidades[sin_cantidad[0]] = resto

    lineas = [
        {"nombre_producto": f"Bidón {capacidad}L {variante}", "cantidad": n}
        for variante, n in cantidades.items()
        if n > 0
    ]
    if not lineas:
        return None
    restante = pendiente - sum(item["cantidad"] for item in lineas)
    nueva_aclaracion = {"capacidad_litros": capacidad, "cantidad": restante} if restante > 0 else None
    # Bidones de la otra capacidad que el mismo mensaje ya trae completos
    # ("2 de 20 recarga y 1 de 12 nuevo"): se agregan aquí también, para no
    # depender de que el LLM los haya extraído.
    otras = [
        {"nombre_producto": f"Bidón {capacidad_mencion}L {variante}", "cantidad": n}
        for variante, n, capacidad_mencion in _menciones_bidon(texto)
        if capacidad_mencion not in (None, capacidad) and n
    ]
    return nueva_aclaracion, lineas + otras


def _aclaracion_tras_turno(
    aclaracion_previa: dict | None,
    aclaracion_llm: dict | None,
    productos_previos: list[dict],
    productos: list[dict],
) -> dict | None:
    """Aclaración "nuevo o recarga" que queda pendiente cuando el código no
    la resolvió. El LLM no puede hacerla desaparecer sin más: si la deja en
    null, solo se descuenta lo que efectivamente se agregó de esa capacidad
    (si no agregó nada, sigue pendiente completa)."""
    if aclaracion_llm:
        return aclaracion_llm
    if not aclaracion_previa:
        return None
    categoria = f"Bidón {aclaracion_previa.get('capacidad_litros')}L"
    agregado = (
        _unidades_por_categoria(productos, None).get(categoria, 0)
        - _unidades_por_categoria(productos_previos, None).get(categoria, 0)
    )
    restante = _cantidad_valida(aclaracion_previa.get("cantidad")) - max(0, agregado)
    return {**aclaracion_previa, "cantidad": restante} if restante > 0 else None


def _actualizar_unidades_pedidas(
    draft_previo: dict,
    productos: list[dict],
    aclaracion: dict | None,
    categorias_modificadas: set[str],
) -> dict[str, int]:
    """Registro, por categoría, de cuántas unidades ha pedido el cliente en
    la conversación. Es independiente de cómo se fusionan las líneas: solo
    sube cuando el draft crece, y solo baja cuando el cliente cambia o quita
    algo explícitamente. Si el draft termina con menos unidades que este
    registro, algo se perdió en el camino y no se muestra el resumen (ver
    _productos_no_registrados)."""
    antes = _unidades_por_categoria(
        draft_previo.get("productos") or [], draft_previo.get("aclaracion_pendiente")
    )
    pedidas = dict(draft_previo["unidades_pedidas"]) if "unidades_pedidas" in draft_previo else dict(antes)
    despues = _unidades_por_categoria(productos, aclaracion)
    for categoria in antes.keys() | despues.keys():
        if categoria in categorias_modificadas:
            pedidas[categoria] = despues.get(categoria, 0)
        else:
            pedidas[categoria] = pedidas.get(categoria, 0) + max(
                0, despues.get(categoria, 0) - antes.get(categoria, 0)
            )
    return {categoria: n for categoria, n in pedidas.items() if n > 0}


def _productos_no_registrados(draft: dict) -> dict[str, int]:
    """Unidades que el cliente pidió en la conversación y que no están en el
    draft (ver _actualizar_unidades_pedidas)."""
    actuales = _unidades_por_categoria(draft.get("productos") or [], draft.get("aclaracion_pendiente"))
    return {
        categoria: pedidas - actuales.get(categoria, 0)
        for categoria, pedidas in (draft.get("unidades_pedidas") or {}).items()
        if pedidas > actuales.get(categoria, 0)
    }


def _resumen_productos_corto(productos: list[dict]) -> str:
    return ", ".join(
        f"{item.get('cantidad', 1)}x {item.get('nombre_producto', '')}" for item in productos
    )


def _menciona_direccion_o_ubicacion(texto: str | None) -> bool:
    if not texto:
        return False
    texto_normalizado = texto.lower()
    return any(frase in texto_normalizado for frase in _FRASES_REABREN_DIRECCION_UBICACION)


def _respuesta_es_de_producto(texto: str | None) -> bool:
    """True si el texto sugerido por el LLM pregunta/habla de productos (ej.
    "¿nuevo o recarga?", "¿Básico o USB?", capacidad de la promo) y no salta
    a otro paso (dirección, ubicación, nombre, "algo más" o el resumen)."""
    if not texto:
        return False
    texto_minusculas = texto.lower()
    if not any(palabra in texto_minusculas for palabra in _PALABRAS_PRODUCTO):
        return False
    if _menciona_direccion_o_ubicacion(texto_minusculas):
        return False
    return not any(
        frase in texto_minusculas
        for frase in ("algo más", "algo mas", "nombre", "confirmas el pedido", "resumen")
    )


def _promo_sin_capacidad(productos: list[dict], notas: str | None) -> bool:
    tiene_promo = any(
        (item.get("nombre_producto") or "").startswith("Promo") for item in productos
    )
    return tiene_promo and not re.search(r"(12|20)", notas or "")


def _datos_cliente(cliente: Cliente | None) -> dict | None:
    """Snapshot plano del cliente identificado por teléfono (sin objetos ORM
    en el draft ni entre sesiones)."""
    if cliente is None:
        return None
    return {
        "id": cliente.id,
        "nombre": cliente.nombre,
        "direccion": (cliente.direccion or "").strip() or None,
        "latitud": float(cliente.latitud) if cliente.latitud is not None else None,
        "longitud": float(cliente.longitud) if cliente.longitud is not None else None,
    }


def _datos_faltantes(draft: dict, cliente: dict | None) -> list[str]:
    """Lista ORDENADA de los datos que faltan para poder mostrar el resumen,
    según el draft y el tipo de cliente. El bot pregunta siempre solo por el
    primero (de a un paso).

    - Cliente existente: producto → confirmar dirección registrada (o, si no
      tiene, o si dijo que no, dirección nueva + ubicación) → "¿algo más?".
      Nunca se pide el nombre: ya está en la tabla cliente.
    - Cliente nuevo: producto → nombre → dirección + ubicación → "¿algo más?".
    """
    productos = draft.get("productos") or []
    faltantes = []

    # No se avanza a la dirección mientras quede un bidón sin variante
    # ("nuevo o recarga") o una línea inválida (fuera del catálogo, o con
    # cantidad 0); _fusionar_productos ya las filtra, esto es defensivo.
    if (
        not productos
        or draft.get("aclaracion_pendiente")
        or _promo_sin_capacidad(productos, draft.get("notas"))
        or any(
            item.get("nombre_producto") not in CATALOGO_NOMBRES
            or _cantidad_valida(item.get("cantidad")) < 1
            for item in productos
        )
    ):
        faltantes.append("producto")

    if cliente is None and not draft.get("nombre_cliente"):
        faltantes.append("nombre")

    usa_direccion_habitual = draft.get("usa_direccion_habitual")
    if cliente is not None and cliente.get("direccion") and usa_direccion_habitual is None:
        faltantes.append("confirmar_direccion")
    elif not usa_direccion_habitual:
        if not draft.get("direccion_texto"):
            faltantes.append("direccion")
        if draft.get("ubicacion") is None and not draft.get("ubicacion_rechazada"):
            faltantes.append("ubicacion")

    if not draft.get("algo_mas_respondido"):
        faltantes.append("algo_mas")

    return faltantes


def _contexto_desde_draft(draft: dict | None) -> dict | None:
    if draft is None:
        return None
    return {
        "intencion": draft.get("intencion"),
        # Con otro nombre que el campo "productos" de la respuesta, para que
        # el LLM no los repita: solo debe extraer los del mensaje actual.
        "productos_en_pedido": draft.get("productos", []),
        "aclaracion_pendiente": draft.get("aclaracion_pendiente"),
        "usa_direccion_habitual": draft.get("usa_direccion_habitual"),
        "direccion_texto": draft.get("direccion_texto"),
        "notas": draft.get("notas"),
        "ubicacion_recibida": draft.get("ubicacion") is not None,
        "ubicacion_rechazada": bool(draft.get("ubicacion_rechazada")),
        "nombre_cliente": draft.get("nombre_cliente"),
        "algo_mas_preguntado": draft.get("paso") == "algo_mas",
        "pregunta_pendiente": draft.get("paso"),
    }


async def _interpretar_con_debug(
    phone: str, message: str, es_cliente_nuevo: bool, context: dict | None
) -> dict:
    # Logging de debug para diagnosticar problemas de interpretación. Usa
    # logger.debug a propósito: no aparece con el nivel INFO por defecto, así
    # que no hace falta quitarlo; si se necesita volver a diagnosticar algo,
    # basta con subir temporalmente el nivel de logging a DEBUG.
    logger.debug("[DEBUG contexto] %s", json.dumps(context, ensure_ascii=False))
    resultado = await interpret_message(
        phone=phone,
        message=message,
        es_cliente_nuevo=es_cliente_nuevo,
        context=context,
    )
    logger.debug("[DEBUG resultado_llm] %s", json.dumps(resultado, ensure_ascii=False))
    return resultado


def _texto_paso_producto(draft: dict, respuesta_llm: str | None) -> str:
    """Pregunta del paso "producto". Se deja pasar el texto del LLM solo si
    efectivamente habla de productos (ej. "¿nuevo o recarga?"); si saltó a
    otro paso (pidió dirección, nombre, "¿algo más?"...) se reemplaza."""
    if _respuesta_es_de_producto(respuesta_llm):
        return respuesta_llm
    aclaracion = draft.get("aclaracion_pendiente")
    if aclaracion:
        return PREGUNTA_ACLARACION_BIDON.format(
            cantidad=aclaracion.get("cantidad", 1),
            capacidad=aclaracion.get("capacidad_litros", ""),
        )
    productos = draft.get("productos") or []
    if productos and _promo_sin_capacidad(productos, draft.get("notas")):
        return PREGUNTA_CAPACIDAD_PROMO
    return PREGUNTA_PRODUCTO


def _texto_paso(
    paso: str,
    faltantes: list[str],
    draft: dict,
    cliente: dict | None,
    respuesta_llm: str | None,
    quiere_agregar_algo: bool,
) -> str:
    if paso == "producto":
        return _texto_paso_producto(draft, respuesta_llm)
    if paso == "nombre":
        return MENSAJE_PEDIR_NOMBRE
    if paso == "confirmar_direccion":
        return PREGUNTA_CONFIRMAR_DIRECCION.format(direccion=cliente["direccion"])
    if paso == "direccion":
        if "ubicacion" not in faltantes:
            return PREGUNTA_DIRECCION_TEXTO
        if cliente is not None and cliente.get("direccion"):
            return PREGUNTA_DIRECCION_NUEVA_Y_UBICACION
        return PREGUNTA_DIRECCION_Y_UBICACION
    if paso == "ubicacion":
        return PREGUNTA_UBICACION
    if paso == "algo_mas":
        return PREGUNTA_QUE_MAS if quiere_agregar_algo else PREGUNTA_ALGO_MAS
    raise ValueError(f"Paso desconocido: {paso}")


def _datos_despacho(draft: dict, cliente: dict | None) -> tuple[str, str, str]:
    """(nombre, dirección de despacho, estado de la ubicación) para el
    resumen previo a confirmar."""
    nombre = cliente["nombre"] if cliente is not None else draft.get("nombre_cliente")
    if cliente is not None and draft.get("usa_direccion_habitual"):
        direccion = cliente["direccion"]
        tiene_coordenadas = cliente.get("latitud") is not None and cliente.get("longitud") is not None
        ubicacion = "registrada" if tiene_coordenadas else "sin ubicación registrada"
    else:
        direccion = draft.get("direccion_texto")
        if draft.get("ubicacion") is not None:
            ubicacion = "compartida por WhatsApp"
        else:
            ubicacion = "no compartida (despacho solo con la dirección escrita)"
    return nombre, direccion, ubicacion


async def _construir_resumen(draft: dict, cliente: dict | None) -> dict:
    resumen = await construir_resumen_pedido(draft.get("productos") or [])
    nombre, direccion, ubicacion = _datos_despacho(draft, cliente)
    lineas_texto = "\n".join(
        f'- {linea["cantidad"]}x {linea["nombre"]} — {_formatear_clp(linea["subtotal"])}'
        for linea in resumen["lineas"]
    )
    bloques = [
        f"Resumen de tu pedido:\n{lineas_texto}\nTotal: {_formatear_clp(resumen['total'])}",
        f"Nombre: {nombre}\nDirección de despacho: {direccion}\nUbicación: {ubicacion}",
    ]
    if draft.get("notas"):
        bloques.append(f"Notas: {draft['notas']}")
    bloques.append("¿Confirmas el pedido? Responde SI para confirmar.")
    return {**resumen, "texto_resumen": "\n\n".join(bloques)}


def _saludo(cliente: dict | None) -> str:
    if cliente is not None:
        return f"¡Hola, {cliente['nombre']}!"
    return "¡Hola!"


def _quitar_saludo_inicial(texto: str) -> str:
    return re.sub(r"^\s*¡?\s*hola\b[^!.?\n]*[!.]?\s*", "", texto, flags=re.IGNORECASE)


async def _responder_siguiente_paso(
    phone: str,
    draft: dict,
    cliente: dict | None,
    respuesta_llm: str | None = None,
    quiere_agregar_algo: bool = False,
) -> tuple[str, str]:
    """Calcula en código el siguiente dato faltante, deja el draft en el
    estado/paso correspondiente y devuelve (paso, texto a enviar). Si no
    falta nada, arma el resumen y deja el draft esperando confirmación."""
    faltantes = _datos_faltantes(draft, cliente)

    if not faltantes:
        no_registrados = _productos_no_registrados(draft)
        if no_registrados:
            # El draft tiene menos unidades de las que el cliente pidió en la
            # conversación: no se muestra un resumen incompleto. Se le dice
            # qué falta y se reabre "¿algo más?"; el registro se iguala al
            # draft para no volver a reclamar lo mismo si responde que no.
            logger.error(
                "[order_flow] Productos pedidos sin registrar para phone=%s: %s (draft: %s)",
                phone,
                no_registrados,
                draft.get("productos"),
            )
            draft = {
                **draft,
                "unidades_pedidas": _unidades_por_categoria(
                    draft.get("productos") or [], draft.get("aclaracion_pendiente")
                ),
                "algo_mas_respondido": False,
                "paso": "algo_mas",
                "estado": "armando",
            }
            draft.pop("resumen", None)
            save_draft(phone, draft)
            faltantes_texto = ", ".join(f"{n}x {categoria}" for categoria, n in no_registrados.items())
            return "algo_mas", MENSAJE_PRODUCTOS_NO_REGISTRADOS.format(
                registrados=_resumen_productos_corto(draft.get("productos") or []),
                faltantes=faltantes_texto,
            )

        try:
            resumen = await _construir_resumen(draft, cliente)
        except ValueError:
            # El LLM devolvió un nombre de producto que no existe en la BD:
            # se descarta y se vuelve a preguntar el producto en vez de caer
            # con un error genérico.
            logger.exception("[order_flow] Productos inválidos en el draft de phone=%s", phone)
            draft = {**draft, "productos": [], "algo_mas_respondido": False}
            return await _responder_siguiente_paso(phone, draft, cliente)
        draft = {**draft, "paso": "confirmacion", "estado": ESTADO_ESPERANDO_CONFIRMACION, "resumen": resumen}
        save_draft(phone, draft)
        return "confirmacion", resumen["texto_resumen"]

    paso = faltantes[0]
    if paso in ("direccion", "ubicacion") and "ubicacion" in faltantes:
        estado = "esperando_ubicacion"
    else:
        estado = "armando"
    draft = {**draft, "paso": paso, "estado": estado}
    draft.pop("resumen", None)
    save_draft(phone, draft)
    return paso, _texto_paso(paso, faltantes, draft, cliente, respuesta_llm, quiere_agregar_algo)


async def _aplicar_resultado_llm(
    phone: str,
    resultado: dict,
    draft_previo: dict | None,
    cliente: dict | None,
    mensaje: str | None = None,
) -> str:
    """Combina lo que extrajo el LLM con el draft previo (sin perder datos ya
    capturados) y responde con el siguiente paso calculado en código."""
    es_primer_turno = draft_previo is None
    draft_previo = draft_previo or {}
    paso_previo = draft_previo.get("paso")
    intencion = resultado.get("intencion")

    # Un pedido en curso nunca se vacía por lo que devuelva el LLM (saludos,
    # interjecciones, mensajes ambiguos): la única forma de vaciarlo es una
    # cancelación EXPLÍCITA, que se intercepta antes en procesar_mensaje. Lo
    # que el LLM extrae en este turno se FUSIONA con lo que ya había (ver
    # _fusionar_productos), nunca lo reemplaza.
    productos_previos = draft_previo.get("productos") or []
    aclaracion_previa = draft_previo.get("aclaracion_pendiente")
    aclaracion_llm = resultado.get("aclaracion_pendiente")
    extraidos = resultado.get("productos") or []
    respuesta_llm = resultado.get("respuesta_sugerida")

    # La respuesta a "¿nuevo o recarga?" se resuelve en código (incluido
    # "2 recargas y 2 nuevos"); lo que el LLM haya extraído de las mismas
    # capacidades en este turno se ignora para no sumarlo dos veces.
    aclaracion_a_resolver = aclaracion_previa or aclaracion_llm
    resolucion = _resolver_aclaracion_bidon(aclaracion_a_resolver, mensaje)
    if resolucion is not None:
        aclaracion_pendiente, lineas = resolucion
        categoria_resuelta = f"Bidón {aclaracion_a_resolver['capacidad_litros']}L"
        productos, categorias_modificadas = _fusionar_productos(
            productos_previos,
            extraidos,
            mensaje,
            excluir_categorias=frozenset(_categoria(linea["nombre_producto"]) for linea in lineas),
        )
        for linea in lineas:
            _sumar_linea(productos, linea["nombre_producto"], linea["cantidad"])
        if (
            aclaracion_pendiente is None
            and aclaracion_llm
            and f"Bidón {aclaracion_llm.get('capacidad_litros')}L" != categoria_resuelta
        ):
            # El mismo mensaje trae otro bidón ambiguo, de la otra capacidad.
            aclaracion_pendiente = aclaracion_llm
        # El texto del LLM pudo preguntar algo que el código ya resolvió.
        respuesta_llm = None
    else:
        productos, categorias_modificadas = _fusionar_productos(productos_previos, extraidos, mensaje)
        aclaracion_pendiente = _aclaracion_tras_turno(
            aclaracion_previa, aclaracion_llm, productos_previos, productos
        )
        if (
            aclaracion_previa
            and aclaracion_pendiente == aclaracion_previa
            and not aclaracion_llm
            and _PATRON_MODIFICACION.search(_normalizar_texto(mensaje))
        ):
            # "ya no quiero los bidones": descarta explícitamente el bidón
            # que esperaba "nuevo o recarga".
            aclaracion_pendiente = None
            categorias_modificadas.add(f"Bidón {aclaracion_previa.get('capacidad_litros')}L")
    unidades_pedidas = _actualizar_unidades_pedidas(
        draft_previo, productos, aclaracion_pendiente, categorias_modificadas
    )
    productos_cambiaron = productos != productos_previos

    # Datos ya capturados nunca se pierden ni se vuelven a pedir: un valor
    # nulo del LLM no borra lo que ya estaba en el draft.
    nombre_cliente = draft_previo.get("nombre_cliente")
    if cliente is None:
        nombre_cliente = resultado.get("nombre_cliente") or nombre_cliente
        if not nombre_cliente and paso_previo == "nombre":
            nombre_cliente = _nombre_desde_texto(mensaje)

    usa_direccion_habitual = draft_previo.get("usa_direccion_habitual")
    direccion_texto = draft_previo.get("direccion_texto")
    ubicacion_rechazada = bool(draft_previo.get("ubicacion_rechazada"))
    tiene_direccion_registrada = cliente is not None and bool(cliente.get("direccion"))

    if not tiene_direccion_registrada:
        usa_direccion_habitual = False
    elif usa_direccion_habitual is None:
        if resultado.get("direccion_texto"):
            usa_direccion_habitual = False
        elif paso_previo == "confirmar_direccion":
            # Respuesta a "¿Despachamos a {dirección}?". La negativa se
            # evalúa primero ("sí, pero es otra" → dirección nueva).
            if _pide_direccion_distinta(mensaje):
                usa_direccion_habitual = False
            elif resultado.get("usa_direccion_habitual") or _es_afirmativa(mensaje):
                usa_direccion_habitual = True

    if not usa_direccion_habitual:
        direccion_texto = resultado.get("direccion_texto") or direccion_texto
        ubicacion_rechazada = ubicacion_rechazada or bool(resultado.get("ubicacion_rechazada"))

    notas = resultado.get("notas") or draft_previo.get("notas")

    algo_mas_respondido = bool(draft_previo.get("algo_mas_respondido"))
    quiere_agregar_algo = False
    if productos_cambiaron and productos_previos:
        algo_mas_respondido = False
    elif paso_previo == "algo_mas":
        # "sí" a "¿algo más?" significa que quiere agregar algo, aunque el
        # LLM lo marque como pedido_completo (visto con "Si" a secas).
        quiere_agregar_algo = _es_afirmativa(mensaje) and not _no_quiere_nada_mas(mensaje)
        algo_mas_respondido = not quiere_agregar_algo and (
            bool(resultado.get("pedido_completo")) or _no_quiere_nada_mas(mensaje)
        )

    nuevo_draft = {
        "intencion": "pedido" if productos else intencion,
        "productos": productos,
        "aclaracion_pendiente": aclaracion_pendiente,
        "notas": notas,
        "nombre_cliente": nombre_cliente,
        "usa_direccion_habitual": usa_direccion_habitual,
        "direccion_texto": direccion_texto,
        "ubicacion": draft_previo.get("ubicacion"),
        "ubicacion_rechazada": ubicacion_rechazada,
        "algo_mas_respondido": algo_mas_respondido,
        "unidades_pedidas": unidades_pedidas,
    }

    # Salvaguarda de "sin productos no hay '¿algo más?' ni resumen": como
    # "producto" es siempre el primer dato faltante cuando la lista está
    # vacía (ver _datos_faltantes), con un draft sin productos el paso
    # calculado es "producto" sin importar qué haya respondido el LLM (que en
    # producción llegó a preguntar "¿algo más?" sin productos). Se verifica
    # explícitamente para que un cambio futuro en el orden no lo rompa.
    paso, texto = await _responder_siguiente_paso(
        phone, nuevo_draft, cliente, respuesta_llm, quiere_agregar_algo
    )
    if not productos and paso != "producto":
        logger.error("[order_flow] Paso '%s' calculado sin productos para phone=%s", paso, phone)
        save_draft(phone, {**nuevo_draft, "paso": "producto", "estado": "armando"})
        paso, texto = "producto", PREGUNTA_PRODUCTO

    if intencion in _INTENCIONES_RESPUESTA_LLM and resultado.get("respuesta_sugerida"):
        # Consulta de precio/pedidos o tema fuera de alcance: se responde lo
        # que preguntó y, si hay un pedido en curso, se retoma el paso
        # pendiente a continuación.
        texto = (
            f"{resultado['respuesta_sugerida']}\n\n{texto}"
            if productos
            else resultado["respuesta_sugerida"]
        )
    elif productos_previos and not productos_cambiaron and intencion != "pedido" and paso != "confirmacion":
        texto = f"Sigo con tu pedido de {_resumen_productos_corto(productos)}. {texto}"

    if es_primer_turno:
        texto = f"{_saludo(cliente)} {_quitar_saludo_inicial(texto)}"

    return texto


async def _aplicar_ubicacion(
    phone: str, location: dict | None, draft_previo: dict | None, cliente: dict | None
) -> str:
    """Ubicación de WhatsApp recibida, en cualquier estado del flujo. Solo
    se agregan las coordenadas al draft (sin pasar por el LLM, para que nada
    de lo ya capturado —nombre, dirección, productos— se pierda) y se
    responde con el siguiente paso calculado en código."""
    draft = dict(draft_previo or {})
    tiene_direccion_registrada = cliente is not None and bool(cliente.get("direccion"))

    if draft.get("usa_direccion_habitual") and tiene_direccion_registrada:
        # Ya confirmó su dirección registrada: se despacha con las
        # coordenadas guardadas del cliente, esta ubicación no se usa.
        _, texto = await _responder_siguiente_paso(phone, draft, cliente)
        return texto

    ubicacion = location or {}
    draft["ubicacion"] = {
        "latitud": ubicacion.get("latitude"),
        "longitud": ubicacion.get("longitude"),
    }
    if tiene_direccion_registrada and draft.get("usa_direccion_habitual") is None:
        # Compartir una ubicación mientras se le pregunta por su dirección
        # registrada equivale a indicar una dirección distinta.
        draft["usa_direccion_habitual"] = False

    _, texto = await _responder_siguiente_paso(phone, draft, cliente)
    texto = f"{MENSAJE_UBICACION_RECIBIDA} {texto}"
    if draft_previo is None:
        texto = f"{_saludo(cliente)} {texto}"
    return texto


async def _escalar_telefono_duplicado(phone: str, clientes: list[Cliente]) -> str:
    """Más de un cliente con el mismo teléfono: el bot no elige uno. Se avisa
    a la ejecutiva, se cierra la conversación del bot (los mensajes
    siguientes van a la ejecutiva, ver whatsapp.py) y se le avisa al cliente
    que lo contactarán."""
    ids = ", ".join(str(cliente.id) for cliente in clientes)
    logger.warning("[order_flow] Teléfono %s registrado en varios clientes (ids %s)", phone, ids)
    clear_draft(phone)
    try:
        await send_whatsapp_message(
            to=settings.EJECUTIVA_PHONE,
            message=MENSAJE_NOTIFICACION_DUPLICADO.format(telefono=phone, ids=ids),
        )
    except Exception:
        logger.exception("[order_flow] Falló avisar a la ejecutiva del teléfono duplicado %s", phone)
    await marcar_inactiva(phone)
    return MENSAJE_CLIENTE_DUPLICADO


async def _buscar_clientes(phone: str) -> list[Cliente]:
    async with SessionLocal() as session:
        return await buscar_clientes_por_telefono(session, phone)


def _agregar_auditoria_cliente(
    session, cliente: Cliente, accion: str, antes: dict | None
) -> None:
    # Se agrega a la MISMA sesión que el pedido (no vía registrar_auditoria,
    # que abre su propia sesión) para que quede en la misma transacción.
    session.add(
        Auditoria(
            usuario=USUARIO_AUDITORIA_BOT,
            entidad="cliente",
            entidad_id=cliente.id,
            accion=accion,
            antes=antes,
            despues=construir_snapshot(cliente),
        )
    )


async def _confirmar_pedido(phone: str, draft: dict | None) -> str:
    if draft is None:
        # Idempotencia: un mensaje duplicado (ej. reintento de webhook) puede
        # llegar después de que el primero ya confirmó el pedido y limpió el
        # draft. En vez de fallar o crear un pedido nuevo, respondemos con un
        # mensaje neutro.
        return "Tu pedido ya fue confirmado anteriormente."

    # Creación/actualización del cliente, su auditoría y el pedido con sus
    # detalles van en UNA sola transacción: si algo falla, rollback de todo
    # (no queda un pedido sin el cliente actualizado ni al revés).
    async with SessionLocal() as session:
        clientes = await buscar_clientes_por_telefono(session, phone)
        if len(clientes) > 1:
            return await _escalar_telefono_duplicado(phone, clientes)
        cliente = clientes[0] if clientes else None

        faltantes = _datos_faltantes(draft, _datos_cliente(cliente))
        if faltantes:
            # Defensivo: el cliente pudo cambiar en la BD entre el resumen y
            # el "SI" (ej. la ejecutiva borró su dirección). Se retoma el
            # paso que falte en vez de crear un pedido incompleto.
            _, texto = await _responder_siguiente_paso(phone, draft, _datos_cliente(cliente))
            return texto

        try:
            ubicacion = draft.get("ubicacion") or {}
            latitud = ubicacion.get("latitud")
            longitud = ubicacion.get("longitud")

            if cliente is None:
                cliente = Cliente(
                    telefono=normalizar_telefono(phone),
                    nombre=draft["nombre_cliente"],
                    direccion=draft["direccion_texto"],
                    latitud=latitud,
                    longitud=longitud,
                    activo=True,
                    opt_out_whatsapp=False,
                )
                session.add(cliente)
                await session.flush()
                await session.refresh(cliente)
                _agregar_auditoria_cliente(session, cliente, "crear", antes=None)
            elif not draft.get("usa_direccion_habitual"):
                # Dirección nueva: reemplaza la registrada y sus coordenadas
                # (null si no compartió ubicación, para no dejar las de la
                # dirección anterior).
                snapshot_antes = construir_snapshot(cliente)
                cliente.direccion = draft["direccion_texto"]
                cliente.latitud = latitud
                cliente.longitud = longitud
                await session.flush()
                await session.refresh(cliente)
                _agregar_auditoria_cliente(session, cliente, "actualizar", antes=snapshot_antes)

            resumen = draft.get("resumen") or {}
            lineas = resumen.get("lineas", [])

            nombres_productos = [linea["nombre"] for linea in lineas]
            result = await session.execute(
                select(Producto).where(Producto.nombre.in_(nombres_productos))
            )
            productos_bd = {p.nombre: p for p in result.scalars().all()}

            # En este punto el cliente ya tiene la dirección de despacho de
            # este pedido (la registrada que confirmó, o la nueva recién
            # guardada), así que el pedido la copia tal cual.
            pedido = Pedido(
                cliente_id=cliente.id,
                estado=EstadoPedido.PENDIENTE,
                direccion_despacho=cliente.direccion,
                latitud=cliente.latitud,
                longitud=cliente.longitud,
                total=resumen.get("total", 0),
            )
            session.add(pedido)
            await session.flush()

            for linea in lineas:
                producto = productos_bd[linea["nombre"]]
                session.add(
                    DetallePedido(
                        pedido_id=pedido.id,
                        producto_id=producto.id,
                        cantidad_solicitada=linea["cantidad"],
                        precio_unitario=linea["precio_unitario"],
                    )
                )

            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("[order_flow] Falló la creación del pedido para phone=%s", phone)
            return MENSAJE_ERROR_PEDIDO

    clear_draft(phone)
    await marcar_inactiva(phone)
    return (
        f"¡Pedido #{pedido.id} confirmado! Quedó pendiente de revisión, "
        "te contactaremos para coordinar la entrega."
    )


async def _es_cliente_nuevo(phone: str) -> bool:
    return not await _buscar_clientes(phone)


async def procesar_mensaje(
    phone: str,
    message_type: str,
    message_text: str | None,
    location: dict | None,
) -> str:
    # Serializa el procesamiento de mensajes de un mismo teléfono: si llegan
    # dos mensajes del mismo cliente en paralelo (ej. reintento de webhook),
    # el segundo espera a que el primero termine por completo (incluyendo
    # cualquier escritura en el draft o creación de pedido) antes de empezar.
    async with get_lock(phone):
        # Identificación por teléfono (normalizado) en cada mensaje: si hay
        # más de un cliente con ese teléfono, no se elige uno.
        clientes = await _buscar_clientes(phone)
        if len(clientes) > 1:
            return await _escalar_telefono_duplicado(phone, clientes)
        cliente = _datos_cliente(clientes[0]) if clientes else None

        draft = get_draft(phone)
        estado = draft.get("estado") if draft else None

        # Cancelación EXPLÍCITA del pedido/estado en curso: se intercepta antes
        # de llamar al LLM y antes de cualquier otra rama del flujo, con
        # cualquier draft (con o sin productos). clear_draft(phone) borra el
        # draft completo, y como la tabla cliente solo se escribe al
        # confirmar, cancelar no deja ningún cambio en cliente. Si no es claro
        # que quiera cancelar (ver _intencion_cancelar), se le pregunta.
        if draft is not None and message_type == "text":
            cancelacion = _intencion_cancelar(
                message_text, texto_libre=draft.get("paso") in _PASOS_TEXTO_LIBRE
            )
            if cancelacion == "cancelar":
                clear_draft(phone)
                return MENSAJE_PEDIDO_CANCELADO
            if cancelacion == "duda":
                if estado in (ESTADO_ESPERANDO_CONFIRMACION, ESTADO_ESPERANDO_MODIFICACION):
                    # Tras esta pregunta un "sí" no debe confirmar el pedido.
                    save_draft(phone, {**draft, "estado": ESTADO_ESPERANDO_MODIFICACION})
                return PREGUNTA_CANCELAR

        if message_type == "location":
            return await _aplicar_ubicacion(phone, location, draft, cliente)

        # Solo se confirma con un sí explícito y si el último mensaje del bot
        # fue el resumen. Cualquier otra respuesta no confirma: una negativa
        # pregunta qué cambiar, y el resto (cambios, consultas) pasa al LLM,
        # que vuelve a armar el resumen si no falta nada.
        if estado == ESTADO_ESPERANDO_CONFIRMACION:
            if _es_confirmacion_explicita(message_text):
                return await _confirmar_pedido(phone, draft)
            if _es_negativa_simple(message_text):
                save_draft(phone, {**draft, "estado": ESTADO_ESPERANDO_MODIFICACION})
                return MENSAJE_NO_CONFIRMADO
        elif estado == ESTADO_ESPERANDO_MODIFICACION:
            if _es_confirmacion_explicita(message_text):
                # Respondió "no" al resumen y ahora "sí": no se confirma (¿sí a
                # qué?). Se muestra de nuevo el resumen para que confirme
                # explícitamente.
                paso, texto = await _responder_siguiente_paso(phone, draft, cliente)
                return f"{PREFIJO_REPETIR_RESUMEN}\n\n{texto}" if paso == "confirmacion" else texto
            if _es_negativa_simple(message_text):
                return PREGUNTA_CANCELAR

        resultado = await _interpretar_con_debug(
            phone, message_text or "", cliente is None, _contexto_desde_draft(draft)
        )
        return await _aplicar_resultado_llm(phone, resultado, draft, cliente, message_text)
