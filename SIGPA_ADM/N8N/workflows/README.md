# workflows/

Los workflows de n8n viven en el volumen de Docker (`sigpa_n8n_data`), que **no** se sube al repo.
Para que queden versionados en git, cada workflow se exporta como JSON a esta carpeta.

## Convención

- Un archivo por workflow, en kebab-case: `optimizacion-rutas.json`.
- Los borradores no importables llevan el sufijo `.placeholder.json`.
- Exportar después de cada cambio relevante y commitear el JSON junto con el cambio.
- Los JSON no deben contener secretos: usar `{{ $env.VARIABLE }}` en los nodos y credenciales de n8n
  (las credenciales no se exportan con el workflow).

## Exportar

- **Desde el editor:** menú `...` del workflow → **Download** → guardar aquí.
- **Desde la CLI** (con el contenedor corriendo):

  ```bash
  docker compose exec n8n n8n export:workflow --all --separate --output=/tmp/wf/
  docker compose cp n8n:/tmp/wf/. ./workflows/
  ```

## Importar

- **Desde el editor:** menú `...` → **Import from File**.
- **Desde la CLI:**

  ```bash
  docker compose cp ./workflows/optimizacion-rutas.json n8n:/tmp/optimizacion-rutas.json
  docker compose exec n8n n8n import:workflow --input=/tmp/optimizacion-rutas.json
  ```

Al importar, las credenciales quedan referenciadas por nombre pero sin valor: hay que asignarlas
en cada nodo que las usa (ver el README de la carpeta `N8N/`).
