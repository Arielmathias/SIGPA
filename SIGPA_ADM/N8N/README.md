# n8n — entorno local de SIGPA

Entorno local (Docker) de [n8n](https://n8n.io) que va a correr el workflow de
optimización de rutas (EP-04).

## Cómo encaja en la arquitectura

La ruta se genera a pedido de la ejecutiva desde el panel, no con un cron:

```
[Panel] --POST /rutas/planificar (JWT)--> [FastAPI] --POST + X-Route-Secret--> [n8n: webhook]
                                              ^                                     |
                                              |       geocodifica (OpenRouteService)
                                              |       y optimiza el orden de las paradas
                                              +---------- respuesta JSON <----------+
```

- **El backend FastAPI arma las paradas** desde la BD con los pedidos que eligió la ejecutiva,
  llama al webhook de n8n de forma síncrona, **valida** la respuesta y la **persiste** (pedidos a
  "confirmado" con su `orden_entrega`). Ver `Backend/app/services/ruta_service.py`.
- **n8n geocodifica y optimiza:** recibe las paradas, geocodifica las que vienen sin coordenadas
  (OpenRouteService), calcula el orden desde el depósito y responde. Las coordenadas del depósito
  viven solo en n8n.
- **n8n no escribe en la base de datos** ni se conecta a Supabase.

### Contrato con el backend

Request que envía el backend (`POST` al webhook, header `X-Route-Secret`):

```json
{"paradas": [{"pedido_id": 12, "direccion_texto": "Santa Maria 793", "latitud": -33.1229, "longitud": -71.5709},
             {"pedido_id": 15, "direccion_texto": "Los Pinos 456, Quilpué", "latitud": null, "longitud": null},
             {"pedido_id": 18, "direccion_texto": "Pasaje sin número", "latitud": null, "longitud": null}]}
```

Respuesta que debe devolver n8n (200, JSON):

```json
{"ruta": [{"pedido_id": 15, "orden_entrega": 1, "latitud": -33.0478, "longitud": -71.4412},
          {"pedido_id": 12, "orden_entrega": 2, "latitud": -33.1229, "longitud": -71.5709}],
 "sin_resolver": [{"pedido_id": 18, "motivo": "No se pudo geocodificar la dirección"}]}
```

Reglas que el backend valida (si no se cumplen, no guarda nada y responde error al panel):

- Cada pedido enviado aparece **exactamente una vez**, en `ruta` o en `sin_resolver`; ningún
  `pedido_id` que no se haya enviado.
- `orden_entrega`: enteros positivos, sin repetir (1 = primera parada).
- `latitud` en [-90, 90] y `longitud` en [-180, 180].
- El webhook debe responder antes de `N8N_ROUTE_TIMEOUT_SECONDS` (default 45 s del lado del backend).

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

Dentro de los nodos, las variables del `.env` se leen como `{{ $env.SIGPA_ROUTE_WEBHOOK_SECRET }}`,
`{{ $env.OPENROUTESERVICE_API_KEY }}`, etc.

Para probar con el backend corriendo en local, el backend debe apuntar al webhook de este
contenedor: `N8N_ROUTE_WEBHOOK_URL=http://localhost:5678/webhook/sigpa-ruta` (o la URL de test
`/webhook-test/...` mientras el workflow no esté activo).

## Workflows: exportar e importar

El volumen de Docker no se versiona, así que cada workflow se exporta como JSON a
[`workflows/`](workflows/README.md) y se commitea. Ahí está el detalle de exportar/importar
desde el editor y desde la CLI.

## Qué falta para que el workflow real funcione

1. **Construir el workflow** `optimizacion-rutas`: nodo Webhook (POST) → validar el header
   `X-Route-Secret` → geocodificar las paradas sin coordenadas → optimizar el orden desde el
   depósito → responder con el contrato de arriba. Hoy solo existe el borrador
   `workflows/optimizacion-rutas.placeholder.json`.
2. **API key de OpenRouteService** — generarla en <https://openrouteservice.org> (nivel gratuito)
   y ponerla en `OPENROUTESERVICE_API_KEY`.
3. **Coordenadas del depósito** en `DEPOSITO_LATITUD` / `DEPOSITO_LONGITUD`.
4. **Secreto compartido:** el mismo valor en `SIGPA_ROUTE_WEBHOOK_SECRET` (aquí) y en
   `N8N_ROUTE_WEBHOOK_SECRET` (backend). En el backend, además, `N8N_ROUTE_WEBHOOK_URL` con la
   URL de producción del webhook.
