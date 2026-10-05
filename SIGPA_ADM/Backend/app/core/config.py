import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class Settings(BaseSettings):
    PROJECT_NAME: str = "SIGPA"

    META_WHATSAPP_TOKEN: str = ""
    META_PHONE_NUMBER_ID: str = ""
    META_WABA_ID: str = ""
    META_VERIFY_TOKEN: str = ""
    META_API_URL: str = ""

    DATABASE_URL: str = ""

    OPENAI_API_KEY: str = ""

    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""

    CORS_ORIGINS: str = "http://localhost:5173"

    EJECUTIVA_PHONE: str = "56957721243"

    INTERNAL_CRON_SECRET: str = ""

    # Webhook de n8n que geocodifica y optimiza la ruta de reparto (EP-04).
    # El backend lo llama desde POST /rutas/planificar (ver ruta_service).
    N8N_ROUTE_WEBHOOK_URL: str = ""
    N8N_ROUTE_WEBHOOK_SECRET: str = ""
    N8N_ROUTE_TIMEOUT_SECONDS: float = 45

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origins_list(self) -> list[str]:
        # Convierte el string de la variable de entorno en una lista
        return [origen.strip() for origen in self.CORS_ORIGINS.split(",") if origen.strip()]


settings = Settings()

MENSAJE_SALUDO_ESPONTANEO = (
    "¡Hola! Gracias por escribirnos. Una ejecutiva revisará tu mensaje y te contactará a la brevedad."
)

MENSAJE_NOTIFICACION_EJECUTIVA = (
    "Hola, un cliente ({telefono}) escribió al WhatsApp de pedidos. "
    "Por favor revisa y continúa la conversación."
)
