const API_URL = 'https://api-repuestos-guadanas.onrender.com';

function getSession() {
  const data = localStorage.getItem('sesion_guadanas');
  return data ? JSON.parse(data) : null;
}

async function registrarAuditoria(tabla, registroId, accion, anterior = null, nuevo = null) {
  const sesion = getSession();
  if (!sesion) return;

  await db.auditoria_logs.add({
    id: crypto.randomUUID(),
    usuario_id: sesion.usuario.id,
    tabla_afectada: tabla,
    registro_id: registroId,
    accion: accion,
    detalle_anterior: anterior,
    detalle_nuevo: nuevo,
    fecha_hora: new Date().toISOString(),
    sincronizado: 0
  });
}

async function sincronizarDatos() {
  const sesion = getSession();
  if (!sesion || !navigator.onLine) return;

  const btnSync = document.getElementById('btn-sync');
  if (btnSync) btnSync.textContent = 'Sincronizando...';

  try {
    const repuestosPendientes = await db.repuestos.where('sincronizado').equals(0).toArray();
    const eliminadosPendientes = await db.repuestos_eliminados.toArray();
    const ventasPendientes = await db.ventas.where('sincronizado').equals(0).toArray();
    const gastosPendientes = await db.gastos.where('sincronizado').equals(0).toArray();
    const logsPendientes = await db.auditoria_logs.where('sincronizado').equals(0).toArray();

    for (let v of ventasPendientes) {
      v.detalles = await db.detalle_ventas.where('venta_id').equals(v.id).toArray();
    }

    if (
      repuestosPendientes.length > 0 ||
      eliminadosPendientes.length > 0 ||
      ventasPendientes.length > 0 ||
      gastosPendientes.length > 0 ||
      logsPendientes.length > 0
    ) {
      const pushRes = await fetch(`${API_URL}/sync/push`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${sesion.access_token}`
        },
        body: JSON.stringify({
          repuestos: repuestosPendientes,
          eliminados: eliminadosPendientes.map(e => e.id),
          ventas: ventasPendientes,
          gastos: gastosPendientes,
          auditoria: logsPendientes
        })
      });

      if (pushRes.ok) {
        await db.repuestos.bulkPut(repuestosPendientes.map(r => ({
          ...r,
          stock_actual: Math.max(0, r.stock_actual),
          sincronizado: 1
        })));
        await db.repuestos_eliminados.clear();
        await db.ventas.bulkPut(ventasPendientes.map(v => {
          const { detalles, ...ventaSinDetalles } = v;
          return { ...ventaSinDetalles, sincronizado: 1 };
        }));
        await db.gastos.bulkPut(gastosPendientes.map(g => ({ ...g, sincronizado: 1 })));
        await db.auditoria_logs.bulkPut(logsPendientes.map(l => ({ ...l, sincronizado: 1 })));
      }
    }

    const lastSync = localStorage.getItem('last_sync_time');
    const urlPull = lastSync ? `${API_URL}/sync/pull?since=${encodeURIComponent(lastSync)}` : `${API_URL}/sync/pull`;

    const pullRes = await fetch(urlPull, {
      headers: { 'Authorization': `Bearer ${sesion.access_token}` }
    });

    if (pullRes.ok) {
      const data = await pullRes.json();
      if (data.usuarios?.length) await db.usuarios.bulkPut(data.usuarios);
      if (data.repuestos?.length) {
        await db.repuestos.bulkPut(data.repuestos.map(r => ({
          ...r,
          stock_actual: Math.max(0, r.stock_actual),
          sincronizado: 1
        })));
      }
      if (data.ventas?.length) {
        for (const v of data.ventas) {
          const { detalles, ...ventaBase } = v;
          await db.ventas.put({ ...ventaBase, sincronizado: 1 });
          if (detalles?.length) {
            await db.detalle_ventas.bulkPut(detalles);
          }
        }
      }
      if (data.gastos?.length) {
        await db.gastos.bulkPut(data.gastos.map(g => ({ ...g, sincronizado: 1 })));
      }
      if (data.auditoria?.length) {
        await db.auditoria_logs.bulkPut(data.auditoria.map(a => ({ ...a, sincronizado: 1 })));
      }
      localStorage.setItem('last_sync_time', data.server_time);
    }

    if (typeof renderizarTodo === 'function') await renderizarTodo();
  } catch (err) {
    console.warn('Sin conexión estable para sincronizar:', err);
  } finally {
    if (btnSync) btnSync.textContent = 'Sincronizar';
  }
}

window.addEventListener('online', sincronizarDatos);