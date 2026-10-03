"""Procesamiento en segundo plano de los mensajes que llegan al webhook.

El webhook responde 200 a Meta de inmediato y deja el trabajo (LLM, BD,
respuesta por WhatsApp) en una cola FIFO por teléfono: los mensajes de un
mismo teléfono se procesan de a uno y en el orden en que llegaron, y los de
teléfonos distintos en paralelo.

Además lleva un registro en memoria de los ids de mensaje de WhatsApp
(wamid) ya recibidos, para descartar en el acto una entrega repetida del
mismo mensaje. Es la primera barrera de idempotencia; la segunda (que
sobrevive a reinicios) es la tabla mensaje_whatsapp, ver
registrar_mensaje_entrante.

Igual que draft_store, todo esto vive en memoria del proceso: sirve para un
solo proceso/worker (el Dockerfile levanta uvicorn sin --workers). Si se
escala a varios procesos, la cola y el registro de wamids deben migrarse a
algo compartido (ej. Redis).
"""

import asyncio
import logging
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Cantidad de wamids recientes que se recuerdan en memoria. Meta reintenta
# en minutos/horas; los más antiguos igual los descarta la BD.
MAX_WAMIDS_RECIENTES = 5000

_wamids_recientes: OrderedDict[str, None] = OrderedDict()

_colas: dict[str, deque[Callable[[], Awaitable[None]]]] = {}

_workers: dict[str, asyncio.Task] = {}


def reservar_wamid(wamid: str) -> bool:
    """True si es la primera vez que se ve este wamid (y lo marca como
    visto); False si es una entrega repetida. Sin awaits: es atómica dentro
    del event loop, así que dos POST simultáneos del mismo mensaje no pueden
    pasar ambos."""
    if wamid in _wamids_recientes:
        return False
    _wamids_recientes[wamid] = None
    while len(_wamids_recientes) > MAX_WAMIDS_RECIENTES:
        _wamids_recientes.popitem(last=False)
    return True


def olvidar_wamids() -> None:
    """Solo para pruebas: simula un reinicio del proceso."""
    _wamids_recientes.clear()


def encolar(phone: str, tarea: Callable[[], Awaitable[None]]) -> None:
    """Agrega la tarea a la cola del teléfono y, si no hay un worker
    consumiéndola, lo lanza. No hace awaits, así que el orden de llegada al
    webhook es el orden de procesamiento."""
    _colas.setdefault(phone, deque()).append(tarea)
    if phone not in _workers:
        _workers[phone] = asyncio.create_task(_consumir(phone))


async def _consumir(phone: str) -> None:
    cola = _colas[phone]
    try:
        while cola:
            tarea = cola.popleft()
            try:
                await tarea()
            except Exception:
                logger.exception("[webhook_queue] Falló el procesamiento de un mensaje de %s", phone)
    finally:
        _workers.pop(phone, None)
        _colas.pop(phone, None)


async def esperar_pendientes() -> None:
    """Espera a que se vacíen todas las colas (para pruebas)."""
    while _workers:
        await asyncio.gather(*list(_workers.values()), return_exceptions=True)
