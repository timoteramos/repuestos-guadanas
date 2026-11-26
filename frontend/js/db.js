const db = new Dexie('RepuestosGuadanasLocalDB');

db.version(1).stores({
  usuarios: 'id, nombre, rol, activo',
  repuestos: 'id, codigo, nombre, marca_compatible, categoria, updated_at, deleted_at, sincronizado',
  ventas: 'id, usuario_id, created_at, sincronizado',
  detalle_ventas: 'id, venta_id, repuesto_id, sincronizado',
  auditoria_logs: 'id, usuario_id, fecha_hora, sincronizado'
});