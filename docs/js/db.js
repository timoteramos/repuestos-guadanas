const db = new Dexie('RepuestosGuadanasLocalDB');

db.version(2).stores({
    usuarios: 'id, nombre, rol, activo',
    repuestos: 'id, codigo, nombre, categoria, marca_compatible, sincronizado',
    ventas: 'id, usuario_id, created_at, sincronizado',
    detalle_ventas: 'id, venta_id, repuesto_id',
    gastos: 'id, categoria, created_at, sincronizado',
    auditoria_logs: 'id, accion, fecha_hora, sincronizado'
});