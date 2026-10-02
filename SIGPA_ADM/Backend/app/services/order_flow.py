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

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Auditoria, Cliente, DetallePedido, Pedido, Producto
from app.models.enums import EstadoPedido
from app.services.agent_service import (
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

CONFIRMACIONES = {"si", "sí", "confirmo", "dale", "ok"}

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
# borrar el pedido, pero esto sí.
_PALABRAS_CANCELACION = ("cancela", "cancelar", "anula", "anular")

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


def _es_cancelacion_explicita(texto: str | None) -> bool:
    if not texto:
        return False
    texto_normalizado = texto.strip().lower()
    return any(palabra in texto_normalizado for palabra in _PALABRAS_CANCELACION)


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


def _resolver_aclaracion_bidon(
    aclaracion: dict | None, productos: list[dict], mensaje: str | None
) -> tuple[dict | None, list[dict]]:
    """Si queda un bidón pendiente de "nuevo o recarga" y el mensaje actual
    dice explícitamente uno de los dos (y no ambos), arma el producto en
    código. Cubre un error reproducible del LLM: con "Quiero 2 bidones de 20
    litros recarga" deja el ítem en aclaracion_pendiente pese al "recarga"."""
    if not aclaracion:
        return aclaracion, productos
    texto = _normalizar_texto(mensaje)
    dice_recarga = "recarga" in texto
    dice_nuevo = bool(re.search(r"\bnuev[oa]s?\b", texto))
    capacidad = aclaracion.get("capacidad_litros")
    if dice_recarga == dice_nuevo or capacidad not in (12, 20):
        return aclaracion, productos
    nombre = f"Bidón {capacidad}L {'Recarga' if dice_recarga else 'Nuevo'}"
    if any(item.get("nombre_producto") == nombre for item in productos):
        return None, productos
    return None, [*productos, {"nombre_producto": nombre, "cantidad": aclaracion.get("cantidad") or 1}]


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

    if (
        not productos
        or draft.get("aclaracion_pendiente")
        or _promo_sin_capacidad(productos, draft.get("notas"))
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
        "productos": draft.get("productos", []),
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
        try:
            resumen = await _construir_resumen(draft, cliente)
        except ValueError:
            # El LLM devolvió un nombre de producto que no existe en la BD:
            # se descarta y se vuelve a preguntar el producto en vez de caer
            # con un error genérico.
            logger.exception("[order_flow] Productos inválidos en el draft de phone=%s", phone)
            draft = {**draft, "productos": [], "algo_mas_respondido": False}
            return await _responder_siguiente_paso(phone, draft, cliente)
        draft = {**draft, "paso": "confirmacion", "estado": "esperando_confirmacion", "resumen": resumen}
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
    # cancelación EXPLÍCITA, que se intercepta antes en procesar_mensaje.
    productos_previos = draft_previo.get("productos") or []
    productos = resultado.get("productos") or productos_previos
    aclaracion_pendiente, productos = _resolver_aclaracion_bidon(
        resultado.get("aclaracion_pendiente"), productos, mensaje
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
    }

    # Salvaguarda de "sin productos no hay '¿algo más?' ni resumen": como
    # "producto" es siempre el primer dato faltante cuando la lista está
    # vacía (ver _datos_faltantes), con un draft sin productos el paso
    # calculado es "producto" sin importar qué haya respondido el LLM (que en
    # producción llegó a preguntar "¿algo más?" sin productos). Se verifica
    # explícitamente para que un cambio futuro en el orden no lo rompa.
    paso, texto = await _responder_siguiente_paso(
        phone, nuevo_draft, cliente, resultado.get("respuesta_sugerida"), quiere_agregar_algo
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
        # confirmar, cancelar no deja ningún cambio en cliente.
        if draft is not None and _es_cancelacion_explicita(message_text):
            clear_draft(phone)
            return MENSAJE_PEDIDO_CANCELADO

        if message_type == "location":
            return await _aplicar_ubicacion(phone, location, draft, cliente)

        if estado == "esperando_confirmacion":
            texto_normalizado = (message_text or "").strip().lower()
            if texto_normalizado in CONFIRMACIONES:
                return await _confirmar_pedido(phone, draft)

        resultado = await _interpretar_con_debug(
            phone, message_text or "", cliente is None, _contexto_desde_draft(draft)
        )
        return await _aplicar_resultado_llm(phone, resultado, draft, cliente, message_text)
