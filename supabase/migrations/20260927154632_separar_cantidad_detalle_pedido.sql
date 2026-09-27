-- Separa la cantidad solicitada de la cantidad entregada en detalle_pedido

alter table public.detalle_pedido
  rename column cantidad to cantidad_solicitada;

alter table public.detalle_pedido
  add column cantidad_entregada integer;

alter table public.detalle_pedido
  add constraint detalle_pedido_cantidad_solicitada_chk
    check (cantidad_solicitada > 0),
  add constraint detalle_pedido_cantidad_entregada_chk
    check (cantidad_entregada is null or cantidad_entregada >= 0);

comment on column public.detalle_pedido.cantidad_solicitada is
  'Cantidad pedida por el cliente al confirmar el pedido';
comment on column public.detalle_pedido.cantidad_entregada is
  'Cantidad realmente entregada en terreno. NULL mientras no se registre la entrega. Puede ser menor o mayor que la solicitada';