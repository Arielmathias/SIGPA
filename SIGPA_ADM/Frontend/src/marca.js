// Información pública de Agua DM para la landing.
// Tomada de las publicaciones de Facebook del negocio: confirmar precios y días con ADM antes de publicar.
export const WHATSAPP = '56990667641';
export const WHATSAPP_VISIBLE = '+56 9 9066 7641';
export const SITIO = 'aguadm.cl';

export const COMUNAS = ['Viña del Mar', 'Recreo', 'Reñaca', 'Concón', 'Quilpué', 'Villa Alemana'];

// dia: índice de Date#getDay (1 = lunes)
export const REPARTO = [
  { dia: 1, nombre: 'Lunes', zona: 'Quilpué y Villa Alemana' },
  { dia: 2, nombre: 'Martes', zona: 'Recreo' },
  { dia: 3, nombre: 'Miércoles', zona: 'Viña Centro' },
  { dia: 4, nombre: 'Jueves', zona: 'Concón' },
];

export const BIDONES = [
  { id: 'bidon-20', nombre: 'Bidón 20 L', imagen: '/marca/bidon-20.jpg', precios: [['Bidón nuevo', 6000], ['Recarga', 2500]] },
  { id: 'bidon-12', nombre: 'Bidón 12 L', imagen: '/marca/bidon-12.jpg', precios: [['Bidón nuevo', 6000], ['Recarga', 2000]] },
];

export const DISPENSADORES = [
  { id: 'dispensador-basico', nombre: 'Dispensador básico', detalle: 'De sobremesa, sin electricidad.', imagen: '/marca/dispensador-basico.jpg', precio: 7000 },
  { id: 'dispensador-usb', nombre: 'Dispensador USB', detalle: 'Bomba recargable que va sobre el bidón.', imagen: '/marca/dispensador-usb.jpg', precio: 7000 },
];

export const PACKS = [
  { id: 'pack-20-basico', nombre: 'Pack 20 L + dispensador básico', detalle: '2 bidones de 20 L y un dispensador básico.', imagen: '/marca/pack-20.jpg', precio: 17000 },
  { id: 'pack-20-usb', nombre: 'Pack 20 L + dispensador USB', detalle: '2 bidones de 20 L y un dispensador USB.', imagen: '/marca/pack-20.jpg', precio: 17000 },
  { id: 'pack-12-basico', nombre: 'Pack 12 L + dispensador básico', detalle: '2 bidones de 12 L y un dispensador básico.', imagen: '/marca/bidon-12.jpg', precio: 17000 },
];

export const pesos = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);

// Abre WhatsApp con el mensaje ya escrito; sin mensaje, abre el chat vacío.
export function enlaceWhatsApp(mensaje) {
  return `https://wa.me/${WHATSAPP}${mensaje ? `?text=${encodeURIComponent(mensaje)}` : ''}`;
}
