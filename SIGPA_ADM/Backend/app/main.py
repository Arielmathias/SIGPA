import os

from fastapi import FastAPI

from app.api.routes import clientes, internal, pedidos, productos, whatsapp
from app.core.config import settings

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(whatsapp.router)
app.include_router(productos.router, prefix="/productos")
app.include_router(clientes.router, prefix="/clientes")
app.include_router(pedidos.router, prefix="/pedidos")
app.include_router(internal.router)


@app.get("/health")
async def health():
    # RENDER_GIT_COMMIT lo define Render en cada deploy: sirve para saber
    # qué commit está corriendo. Es la única variable de entorno expuesta.
    return {"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT") or "desconocido"}
