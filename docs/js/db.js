const db = new Dexie('RepuestosGuadanasLocalDB');

db.version(4).stores({
    usuarios: 'id, nombre, rol, activo',
    repuestos: 'id, codigo, nombre, marca_compatible, created_at, sincronizado',
    repuestos_eliminados: 'id',
    ventas: 'id, usuario_id, metodo_pago, estado, created_at, sincronizado',
    detalle_ventas: 'id, venta_id, repuesto_id',
    gastos: 'id, categoria, created_at, sincronizado',
    auditoria_logs: 'id, accion, fecha_hora, sincronizado'
});

async function limpiarBaseLocalPorCambioCuenta() {
    await Promise.all([
        db.usuarios.clear(),
        db.repuestos.clear(),
        db.repuestos_eliminados.clear(),
        db.ventas.clear(),
        db.detalle_ventas.clear(),
        db.gastos.clear(),
        db.auditoria_logs.clear()
    ]);
    localStorage.removeItem('last_sync_time');
}