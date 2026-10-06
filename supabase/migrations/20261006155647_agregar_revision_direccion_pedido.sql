-- Historia 107: marca de revisión de dirección en pedido.
-- Si tiene texto, el pedido quedó en sin_resolver al generar la ruta (n8n no
-- pudo ubicar la dirección con precisión) y requiere revisión manual; el texto
-- es el motivo que devolvió n8n. NULL = sin revisión pendiente.

alter table public.pedido
  add column motivo_revision_direccion text;

comment on column public.pedido.motivo_revision_direccion is
  'Motivo por el que la dirección no se pudo ubicar al generar la ruta. NULL si no hay revisión pendiente.';