# n8n — entorno local de SIGPA

Entorno local (Docker) de [n8n](https://n8n.io) que va a correr el workflow de
optimización de rutas (EP-04).

## Cómo encaja en la arquitectura

- **El backend FastAPI tiene la lógica.** Toda la automatización pesada (cálculo de rutas,
  acceso a datos, llamadas a servicios externos) vive en el backend.
- **n8n solo orquesta:** dispara el proceso (p. ej. un cron diario) y llama a endpoints REST
  del backend, autenticándose con un secreto compartido.
- **n8n no se conecta directo a Supabase.** Todo dato pasa por el backend.

```
[n8n: Cron diario] --POST + secreto--> [FastAPI: /rutas/planificar] --> Supabase / OpenRouteService
```

## Levantarlo

```bash
cp .env.example .env      # completar los valores
docker compose up -d
docker compose ps         # esperar a que el estado sea "healthy"
```

Detenerlo: `docker compose down` (los datos se conservan en el volumen `sigpa_n8n_data`).
Borrar todo, incluido el volumen: `docker compose down -v` (**se pierden credenciales y workflows no exportados**).

Huso horario: `GENERIC_TIMEZONE` y `TZ` están en `America/Santiago`, así los Cron de n8n
se interpretan en hora de Chile, igual que el resto del proyecto.

## Entrar al editor

1. Abrir <http://localhost:5678>.
2. La primera vez, n8n muestra un formulario para crear la cuenta **owner**: completar
   email y password.
3. Guardar esas credenciales en un gestor de contraseñas. **No** van en el `.env`: desde
   n8n 1.0 el login no se configura por variables de entorno.

La cuenta queda guardada en el volumen `sigpa_n8n_data`, así que sobrevive a
`docker compose down` / `up`. Con `docker compose down -v` se borra y hay que crearla de nuevo.

Dentro de los nodos, las variables del `.env` se leen como `{{ $env.SIGPA_BACKEND_URL }}`,
`{{ $env.SIGPA_INTERNAL_SECRET }}`, etc.

Si el backend corre en local en paralelo, desde el contenedor se alcanza en
`http://host.docker.internal:8000` (no `localhost`).

## Workflows: exportar e importar

El volumen de Docker no se versiona, así que cada workflow se exporta como JSON a
[`workflows/`](workflows/README.md) y se commitea. Ahí está el detalle de exportar/importar
desde el editor y desde la CLI.

## Qué falta para que el workflow real funcione

1. **Endpoint `/rutas/planificar` en el backend FastAPI** — todavía no existe.
   `workflows/optimizacion-rutas.placeholder.json` apunta a él con un TODO.
2. **API key de OpenRouteService** — generarla en <https://openrouteservice.org> (nivel gratuito)
   y ponerla en `OPENROUTESERVICE_API_KEY`.
3. **Header y secreto compartido con el backend** — definir el nombre del header (patrón
   `X-Cron-Secret` de `/internal/recordatorio-diario`) y el valor de `SIGPA_INTERNAL_SECRET`,
   el mismo en el `.env` de n8n y en las variables del backend.
4. **Horario del cron** — definir a qué hora (Chile) se planifican las rutas.
