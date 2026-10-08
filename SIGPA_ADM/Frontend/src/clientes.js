// Campos de texto que el panel permite editar; los vacíos opcionales se guardan como null.
const TEXTO = ['nombre', 'apellido_paterno', 'apellido_materno', 'telefono', 'direccion'];
const OBLIGATORIOS = { nombre: 'El nombre es obligatorio.', telefono: 'El teléfono es obligatorio.' };
export function clienteForm(c) {
  return { ...Object.fromEntries(TEXTO.map(k => [k, c[k] ?? ''])), tipo_cliente_id: c.tipo_cliente_id == null ? '' : String(c.tipo_cliente_id), activo: !!c.activo, opt_out_whatsapp: !!c.opt_out_whatsapp };
}
// Devuelve solo los campos modificados, para que el PATCH y la auditoría reflejen el cambio real.
export function cambiosCliente(original, form) {
  const nuevo = { ...Object.fromEntries(TEXTO.map(k => [k, form[k].trim() || null])), tipo_cliente_id: form.tipo_cliente_id === '' ? null : Number(form.tipo_cliente_id), activo: form.activo, opt_out_whatsapp: form.opt_out_whatsapp };
  for (const [k, mensaje] of Object.entries(OBLIGATORIOS)) if (!nuevo[k]) return { error: mensaje };
  const cambios = Object.fromEntries(Object.entries(nuevo).filter(([k, v]) => v !== (original[k] ?? (typeof v === 'boolean' ? false : null))));
  return { cambios };
}
