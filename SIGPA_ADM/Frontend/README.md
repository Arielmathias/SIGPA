# SIGPA · Frontend inicial

React + Vite. Base preparada contra el backend de `fgaete94/SIGPA`, commit `06345ae46bb1ca5f488d4a50d889b9aa3d78fcdf`.

## Iniciar en el computador

1. Instalar Node.js 22.12 o superior compatible con Vite 7.
2. Abrir esta carpeta (`SIGPA_ADM/Frontend`) en Visual Studio Code.
3. Ejecutar `npm ci` en la terminal.
4. Copiar `.env.example` a `.env.local` y completar:

```dotenv
VITE_SUPABASE_URL=https://TU-PROYECTO.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=TU_CLAVE_PUBLICA
SIGPA_API_TARGET=https://TU-BACKEND.onrender.com
```

La clave debe ser publishable (o anon del proyecto). Nunca usar service_role, secret keys, contraseña de base de datos ni token de Meta en el frontend. El acceso se hace en la pantalla de inicio con una cuenta existente de Supabase; no se crean usuarios desde este panel.

5. Ejecutar `npm run dev` y abrir la dirección local indicada en la terminal. Reiniciar Vite al cambiar variables.

## Conexiones implementadas

- Supabase Auth: inicio con correo/contraseña, sesión, renovación mediante SDK y cierre.
- Todas las llamadas FastAPI llevan el JWT de la sesión.
- GET `/pedidos`, GET `/pedidos/{id}`, GET `/clientes`.
- GET `/rutas/pedidos-pendientes` y POST `/rutas/planificar` con `{pedido_ids:[...]}`.
- El panel pide confirmación antes de planificar porque el endpoint también confirma pedidos y guarda el orden.
- Los errores no se sustituyen por datos ficticios. Los indicadores corresponden a todos los registros devueltos, no a una jornada.
- Los cambios manuales de orden solo afectan la impresión actual. Se indica expresamente en pantalla; no hay endpoint existente para persistirlos. Se pierden al recargar/cerrar sesión.

## Límites de esta primera entrega

No se probó con credenciales ni datos reales. No se modificó el backend, no se enviaron mensajes y no se desplegó. La autorización actual del backend valida usuarios autenticados, sin distinguir administradores: antes de abrir el servicio a usuarios externos, el equipo debe definir y aplicar roles en el servidor.

Faltan: persistencia transaccional del orden manual, fecha de entrega e identidad de ruta, edición de clientes/pedidos, reportes históricos y pruebas de integración en el entorno del equipo. La impresión inicial contiene ID, cliente y dirección; todavía no constituye la hoja de reparto completa con cantidades/productos.

## Despliegue

`npm run build` genera `dist`. El proxy `/api` de Vite funciona SOLO con `npm run dev`. Para producción se necesita un proxy HTTPS del mismo origen que envíe `/api/*` a FastAPI quitando `/api`; no basta subir `dist` para conectar la API. Alternativamente, el equipo puede implementar URL directa y CORS explícito en el backend. `npm run preview` sirve únicamente para revisar la compilación, sin conexión API configurada.

No habilitar CORS universal ni exponer secretos para resolver esto. Las variables VITE son públicas y se incorporan al compilar.

## Verificación

`npm run build` verifica la compilación. `npm test` comprueba que el reordenamiento conserve todos los pedidos y respete los extremos de la lista.

Documentación de autenticación: https://supabase.com/docs/reference/javascript/auth-signinwithpassword y https://supabase.com/docs/reference/javascript/auth-onauthstatechange.
