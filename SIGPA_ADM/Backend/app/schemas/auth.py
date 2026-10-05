from pydantic import BaseModel


# Datos que el frontend envía para iniciar sesión
class LoginRequest(BaseModel):
    email: str
    password: str


# Datos que el frontend envía para renovar la sesión.
# El refresh token de Supabase rota: cada uso devuelve uno nuevo
# y el anterior queda invalidado.
class RefreshRequest(BaseModel):
    refresh_token: str


# Respuesta común de /auth/login y /auth/refresh
class TokenResponse(BaseModel):
    access_token: str      # JWT que se envía en "Authorization: Bearer ..."
    refresh_token: str     # Token para pedir un nuevo access token
    token_type: str = "bearer"
    expires_in: int        # Segundos de validez del access token (3600 por defecto en Supabase)