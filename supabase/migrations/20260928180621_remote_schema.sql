CREATE SEQUENCE "public"."auditoria_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."mensaje_whatsapp_id_seq" AS integer INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START WITH 1 CACHE 1 NO CYCLE;

CREATE TABLE "public"."auditoria" (
  "id"         integer                     NOT NULL DEFAULT nextval('public.auditoria_id_seq'::regclass),
  "usuario"    character varying           NOT NULL,
  "entidad"    character varying           NOT NULL,
  "entidad_id" integer                     NOT NULL,
  "accion"     character varying           NOT NULL,
  "antes"      jsonb,
  "despues"    jsonb,
  "creado_en"  timestamp without time zone NOT NULL DEFAULT now(),
  CONSTRAINT "auditoria_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."auditoria"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."conversacion_bot" (
  "telefono"       character varying           NOT NULL,
  "activa"         boolean                     NOT NULL DEFAULT false,
  "iniciada_en"    timestamp without time zone,
  "actualizada_en" timestamp without time zone NOT NULL DEFAULT now(),
  CONSTRAINT "conversacion_bot_pkey" PRIMARY KEY (telefono)
);

ALTER TABLE "public"."conversacion_bot"
  ENABLE ROW LEVEL SECURITY;

CREATE TABLE "public"."mensaje_whatsapp" (
  "id"              integer                     NOT NULL DEFAULT nextval('public.mensaje_whatsapp_id_seq'::regclass),
  "cliente_id"      integer,
  "pedido_id"       integer,
  "telefono"        character varying           NOT NULL,
  "meta_message_id" character varying,
  "tipo"            character varying           NOT NULL,
  "contenido"       text,
  "estado"          character varying,
  "creado_en"       timestamp without time zone NOT NULL DEFAULT now(),
  CONSTRAINT "mensaje_whatsapp_pkey" PRIMARY KEY (id)
);

ALTER TABLE "public"."mensaje_whatsapp"
  ENABLE ROW LEVEL SECURITY;

ALTER SEQUENCE "public"."auditoria_id_seq" OWNED BY "public"."auditoria"."id";

ALTER TABLE "public"."cliente"
  ADD COLUMN "opt_out_whatsapp" boolean NOT NULL DEFAULT false;

ALTER SEQUENCE "public"."mensaje_whatsapp_id_seq" OWNED BY "public"."mensaje_whatsapp"."id";

CREATE TYPE "public"."direccion_mensaje" AS ENUM (
  'entrante',
  'saliente'
);

ALTER TABLE "public"."mensaje_whatsapp"
  ADD COLUMN "direccion" public.direccion_mensaje NOT NULL;

ALTER TABLE "public"."mensaje_whatsapp"
  ADD CONSTRAINT "mensaje_whatsapp_cliente_id_fkey" FOREIGN KEY (cliente_id) REFERENCES public.cliente(id);

ALTER TABLE "public"."mensaje_whatsapp"
  ADD CONSTRAINT "mensaje_whatsapp_pedido_id_fkey" FOREIGN KEY (pedido_id) REFERENCES public.pedido(id);

CREATE INDEX idx_auditoria_entidad ON public.auditoria USING btree (entidad, entidad_id);

CREATE INDEX idx_mensaje_whatsapp_cliente_id ON public.mensaje_whatsapp USING btree (cliente_id);

CREATE INDEX idx_mensaje_whatsapp_telefono ON public.mensaje_whatsapp USING btree (telefono);

COMMENT ON COLUMN "public"."auditoria"."antes" IS 'Snapshot del registro antes del cambio (null si accion=crear).';

COMMENT ON COLUMN "public"."auditoria"."despues" IS 'Snapshot del registro después del cambio (null si accion=eliminar).';

COMMENT ON COLUMN "public"."cliente"."opt_out_whatsapp" IS 'true si el cliente pidió no recibir más mensajes automáticos de WhatsApp (recordatorio proactivo). Independiente de "activo" (baja comercial).';

COMMENT ON COLUMN "public"."conversacion_bot"."activa" IS 'true mientras el bot espera respuesta a un recordatorio que él mismo inició; false por defecto (conversación espontánea) o tras confirmarse el pedido.';

COMMENT ON COLUMN "public"."mensaje_whatsapp"."contenido" IS 'Texto del mensaje, o resumen legible si es de tipo location/template.';

COMMENT ON COLUMN "public"."mensaje_whatsapp"."meta_message_id" IS 'ID devuelto por la Graph API al enviar un mensaje saliente; null para entrantes (Meta no siempre lo replica igual) o si el envío falló antes de obtener respuesta.';

COMMENT ON TABLE "public"."auditoria" IS 'Registro de cambios críticos hechos vía el panel administrativo (API REST) — requisito no funcional de Trazabilidad del caso de negocio.';

COMMENT ON TABLE "public"."conversacion_bot" IS 'Registra si un teléfono tiene una conversación activa iniciada por el bot (recordatorio proactivo). Si no está activa, un mensaje entrante espontáneo se enruta a la ejecutiva en vez del agente de IA.';

COMMENT ON TABLE "public"."mensaje_whatsapp" IS 'Trazabilidad de cada mensaje entrante/saliente de WhatsApp, según requisito no funcional de Trazabilidad del caso de negocio.';

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."auditoria_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT SELECT, UPDATE, USAGE ON SEQUENCE "public"."mensaje_whatsapp_id_seq" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."auditoria" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."conversacion_bot" TO "anon", "authenticated", "postgres", "service_role";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."mensaje_whatsapp" TO "anon", "authenticated", "postgres", "service_role";

GRANT USAGE ON TYPE "public"."direccion_mensaje" TO "postgres";

