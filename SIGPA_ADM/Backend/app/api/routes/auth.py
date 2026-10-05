import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.config import settings
from app.core.security import get_current_user
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse

router = APIRouter(tags=["auth"])

# Endpoint de Supabase Auth que emite tokens (login y refresh usan el mismo,
# cambiando solo el parámetro grant_type)
SUPABASE_TOKEN_URL = f"{settings.SUPABASE_URL}/auth/v1/token"
SUPABASE_LOGOUT_URL = f"{settings.SUPABASE_URL}/auth/v1/logout"


def _cliente_http() -> httpx.AsyncClient:
    """Cliente HTTP para llamar a Supabase Auth (se reemplaza en las pruebas)."""
    return httpx.AsyncClient(timeout=10)


def _headers_supabase() -> dict:
    # Supabase exige la llave pública (anon/publishable) en todas las llamadas a Auth
    return {"apikey": settings.SUPABASE_ANON_KEY, "Content-Type": "application/json"}


async def _pedir_tokens(grant_type: str, body: dict, mensaje_error: str) -> TokenResponse:
    """Llama a Supabase Auth para obtener tokens y traduce sus errores a respuestas del backend."""
    try:
        async with _cliente_http() as client:
            respuesta = await client.post(
                SUPABASE_TOKEN_URL,
                params={"grant_type": grant_type},
                headers=_headers_supabase(),
                json=body,
            )
    except httpx.RequestError:
        # Supabase no respondió (caída, timeout, problema de red)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de autenticación no disponible",
        )

    if respuesta.status_code == 429:
        # Supabase limita los intentos de login por IP
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos, espere un momento",
        )

    if respuesta.status_code in (400, 401):
        # Credenciales o refresh token inválidos. El mensaje es genérico a propósito:
        # no se indica si falló el correo o la contraseña.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=mensaje_error)

    if respuesta.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error inesperado del servicio de autenticación",
        )

    datos = respuesta.json()
    return TokenResponse(
        access_token=datos["access_token"],
        refresh_token=datos["refresh_token"],
        expires_in=datos["expires_in"],
    )


@router.post("/login", response_model=TokenResponse)
async def login(datos: LoginRequest):
    return await _pedir_tokens(
        grant_type="password",
        body={"email": datos.email, "password": datos.password},
        mensaje_error="Correo o contraseña incorrectos",
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(datos: RefreshRequest):
    return await _pedir_tokens(
        grant_type="refresh_token",
        body={"refresh_token": datos.refresh_token},
        mensaje_error="Sesión expirada, inicie sesión nuevamente",
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    authorization: str = Header(None),
    current_user: dict = Depends(get_current_user),
):
    # get_current_user ya validó el token. Se reenvía el mismo header a Supabase
    # para que invalide la sesión (y con ella el refresh token asociado).
    try:
        async with _cliente_http() as client:
            await client.post(
                SUPABASE_LOGOUT_URL,
                headers={**_headers_supabase(), "Authorization": authorization},
            )
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de autenticación no disponible",
        )