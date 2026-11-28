const API_URL = 'https://api-repuestos-guadanas.onrender.com';

async function sincronizarDatos() {
  if (!navigator.onLine) return;
  const sesion = getSession();
  if (!sesion) return;

  const btnSync = document.getElementById('btn-sync');
  if (btnSync) btnSync.textContent = 'Sincronizando...';

  try {
    // 1. Subir cambios locales pendientes (PUSH)
    const repuestosPendientes = await db.repuestos.where('sincronizado').equals(0).toArray();
    const eliminadosPendientes = await db.repuestos_eliminados.toArray();
    const ventasPendientes = await db.ventas.where('sincronizado').equals(0).toArray();
    const gastosPendientes = await db.gastos.where('sincronizado').equals(0).toArray();
    const logsPendientes = await db.auditoria_logs.where('sincronizado').equals(0).toArray();

    for (const v of ventasPendientes) {
      v.detalles = await db.detalle_ventas.where('venta_id').equals(v.id).toArray();
    }

    if (
      repuestosPendientes.length > 0 ||
      eliminadosPendientes.length > 0 ||
      ventasPendientes.length > 0 ||
      gastosPendientes.length > 0 ||
      logsPendientes.length > 0
    ) {
      const resPush = await fetch(`${API_URL}/sync/push`, {
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

      if (resPush.ok) {
        for (const r of repuestosPendientes) await db.repuestos.update(r.id, { sincronizado: 1 });
        if (eliminadosPendientes.length > 0) {
          await db.repuestos_eliminados.bulkDelete(eliminadosPendientes.map(e => e.id));
        }
        for (const v of ventasPendientes) await db.ventas.update(v.id, { sincronizado: 1 });
        for (const g of gastosPendientes) await db.gastos.update(g.id, { sincronizado: 1 });
        for (const l of logsPendientes) await db.auditoria_logs.update(l.id, { sincronizado: 1 });
      }
    }

    // 2. Descargar datos actualizados de este negocio (PULL)
    const resPull = await fetch(`${API_URL}/sync/pull`, {
      headers: { 'Authorization': `Bearer ${sesion.access_token}` }
    });

    if (resPull.ok) {
      const data = await resPull.json();

      // Actualizar estado de invitación o equipo en sesión actual
      if (data.usuario_actual) {
        sesion.usuario = { ...sesion.usuario, ...data.usuario_actual };
        localStorage.setItem('sesion_guadanas', JSON.stringify(sesion));
        if (typeof actualizarBannerInvitacionYEquipo === 'function') {
          actualizarBannerInvitacionYEquipo(sesion);
        }
      }

      if (data.usuarios) await db.usuarios.bulkPut(data.usuarios);

      if (data.repuestos) {
        const idsServidor = new Set(data.repuestos.map(r => r.id));
        const localesSincronizados = await db.repuestos.where('sincronizado').equals(1).toArray();
        for (const loc of localesSincronizados) {
          if (!idsServidor.has(loc.id)) {
            await db.repuestos.delete(loc.id);
          }
        }
        await db.repuestos.bulkPut(data.repuestos.map(r => ({ ...r, sincronizado: 1 })));
      }

      if (data.ventas) {
        for (const v of data.ventas) {
          const { detalles, ...ventaCabecera } = v;
          await db.ventas.put({ ...ventaCabecera, sincronizado: 1 });
          if (detalles) {
            await db.detalle_ventas.bulkPut(detalles.map(d => ({ ...d, sincronizado: 1 })));
          }
        }
      }

      if (data.gastos) {
        await db.gastos.bulkPut(data.gastos.map(g => ({ ...g, sincronizado: 1 })));
      }

      if (data.auditoria) {
        await db.auditoria_logs.bulkPut(data.auditoria.map(a => ({ ...a, sincronizado: 1 })));
      }
      localStorage.setItem('ultima_sync', data.server_time);
    }
  } catch (err) {
    console.warn('Sincronización en espera (modo offline):', err);
  } finally {
    if (btnSync) btnSync.textContent = 'Sincronizar';
    if (typeof renderizarTodo === 'function') renderizarTodo();
  }
}

window.addEventListener('online', sincronizarDatos);