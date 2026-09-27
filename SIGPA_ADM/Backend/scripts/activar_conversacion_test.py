"""
Script utilitario para dejar una conversación como "activa" en conversacion_bot,
de forma que el bot procese los mensajes de ese teléfono (ver la restricción
"el agente solo procesa conversaciones que él inicia" en whatsapp.py).
No es parte del código final: solo para preparar pruebas manuales.

Uso: python -m scripts.activar_conversacion_test <telefono>
Ejemplo: python -m scripts.activar_conversacion_test 56900000000
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.conversacion_bot_service import marcar_activa


async def main(phone: str) -> None:
    await marcar_activa(phone)
    print(f"Conversación de {phone} marcada como activa. Listo para pruebas.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python -m scripts.activar_conversacion_test <telefono>")
        sys.exit(1)

    telefono = sys.argv[1]
    asyncio.run(main(telefono))
