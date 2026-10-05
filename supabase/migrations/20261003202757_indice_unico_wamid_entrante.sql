-- Idempotencia del webhook de WhatsApp: un mismo mensaje entrante (wamid de
-- Meta) se registra y procesa una sola vez. Meta puede entregar el mismo
-- evento más de una vez (visto el 2026-10-03: tres "hola" en 14 ms). Ver
-- registrar_mensaje_entrante en app/services/mensaje_whatsapp_service.py.
-- Sin CONCURRENTLY: la tabla es chica y las migraciones corren en transacción.
CREATE UNIQUE INDEX uq_mensaje_whatsapp_entrante_meta_message_id
  ON public.mensaje_whatsapp (meta_message_id)
  WHERE direccion = 'entrante' AND meta_message_id IS NOT NULL;
