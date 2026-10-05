-- Orden de entrega del pedido en la ruta de reparto generada (EP-04).
-- latitud/longitud ya existen en pedido (20260913222209_agregar_geolocalizacion).

alter table public.pedido
  add column orden_entrega integer;

alter table public.pedido
  add constraint pedido_orden_entrega_chk
    check (orden_entrega is null or orden_entrega > 0);

comment on column public.pedido.orden_entrega is
  'Posición del pedido en la ruta de reparto generada (1 = primera parada). NULL si no está en una ruta o no se pudo ubicar.';
