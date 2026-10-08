# SIGPA · Frontend inicial

React + Vite. Base preparada contra el backend de `fgaete94/SIGPA`, commit `06345ae46bb1ca5f488d4a50d889b9aa3d78fcdf`.

## Iniciar en el computador

1. Instalar Node.js 22.12 o superior compatible con Vite 7.
2. Abrir esta carpeta (`SIGPA_ADM/Frontend`) en Visual Studio Code.
3. Ejecutar `npm ci` en la terminal.
4. Copiar `.env.example` a `.env.local` e indicar la URL del backend FastAPI:

```dotenv
SIGPA_API_TARGET=https://sigpa-34sy.onrender.com
```

El frontend no usa Supabase ni necesita sus claves: la autenticación pasa por el backend (`/auth`), que actúa como proxy a Supabase Auth. El acceso se hace en la pantalla de inicio con una cuenta existente; no se crean usuarios desde este panel. Nunca poner claves, contraseñas de base de datos ni tokens de Meta en el frontend.

5. Ejecutar `npm run dev` y abrir la dirección local indicada en la terminal. Reiniciar Vite al cambiar variables.

## Conexiones implementadas

- Autenticación vía backend: POST `/api/auth/login`, `/api/auth/refresh` y `/api/auth/logout`. El frontend ya no usa Supabase.
- El refresh token se guarda en `sessionStorage` (sobrevive a recargar la página y se borra al cerrar la pestaña); el access token solo en memoria. El token se renueva antes de expirar y, si el backend responde 401, se renueva una vez y se reintenta; si la renovación es rechazada, se vuelve al login.
- Todas las llamadas FastAPI llevan el JWT de la sesión.
- GET `/pedidos`, GET `/pedidos/{id}`, GET `/clientes`.
- Landing pública: GET `/productos` (solo lectura, sin sesión) para nombres y precios, a través del backend; nunca se conecta a Supabase. Solo se usan nombre y precio. Mientras Render despierta se muestran los últimos precios conocidos (`src/catalogo.js`, `PRECIOS_RESPALDO` en `src/marca.js`). Cada tarjeta se asocia a un producto por su nombre exacto en la tabla `producto`.
- GET `/rutas/pedidos-pendientes` y POST `/rutas/planificar` con `{pedido_ids:[...]}`.
- El panel pide confirmación antes de planificar porque el endpoint también confirma pedidos y guarda el orden.
- Los errores no se sustituyen por datos ficticios. Los indicadores corresponden a todos los registros devueltos, no a una jornada.
- Los cambios manuales de orden solo afectan la impresión actual. Se indica expresamente en pantalla; no hay endpoint existente para persistirlos. Se pierden al recargar/cerrar sesión.

## Límites de esta primera entrega

No se probó con credenciales ni datos reales. No se modificó el backend, no se enviaron mensajes y no se desplegó. La autorización actual del backend valida usuarios autenticados, sin distinguir administradores: antes de abrir el servicio a usuarios externos, el equipo debe definir y aplicar roles en el servidor.

Faltan: persistencia transaccional del orden manual, fecha de entrega e identidad de ruta, edición de clientes/pedidos, reportes históricos y pruebas de integración en el entorno del equipo. La impresión inicial contiene ID, cliente y dirección; todavía no constituye la hoja de reparto completa con cantidades/productos.

## Despliegue

`npm run build` genera `dist`. El proxy `/api` de Vite funciona SOLO con `npm run dev`; no basta subir `dist` para conectar la API. En producción (Render, Static Site) se usa una regla **Rewrite** `/api/*` → `https://sigpa-34sy.onrender.com/*`, que envía las llamadas al backend quitando `/api` desde el mismo origen. El sitio no necesita variables `VITE_SUPABASE_*`.

Esta regla todavía no se ha verificado en Render, en particular que reenvíe el header `Authorization` al backend. Si falla, el plan B es usar URL directa al backend y CORS explícito (solo el origen del sitio) en FastAPI.

`npm run preview` sirve únicamente para revisar la compilación, sin conexión API configurada.

No habilitar CORS universal ni exponer secretos para resolver esto. Las variables VITE son públicas y se incorporan al compilar.

## Verificación

`npm run build` verifica la compilación. `npm test` comprueba que el reordenamiento conserve todos los pedidos y respete los extremos de la lista, y prueba el módulo de sesión (decodificación del JWT, expiración con margen y un único refresh ante llamadas simultáneas).
