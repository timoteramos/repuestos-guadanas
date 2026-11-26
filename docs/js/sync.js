const API_URL = 'https://api-repuestos-guadanas.onrender.com'; // Luego la cambiaremos por tu URL de Render

function getSession() {
  const data = localStorage.getItem('sesion_guadanas');
  return data ? JSON.parse(data) : null;
}

// Registrar quién hizo qué y a qué hora en la base local
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

// Sincronizar cambios locales -> nube y nube -> celular
async function sincronizarDatos() {
  const sesion = getSession();
  if (!sesion || !navigator.onLine) return;

  const btnSync = document.getElementById('btn-sync');
  if (btnSync) btnSync.textContent = 'Sincronizando...';

  try {
    // 1. Buscar registros pendientes de subir (sincronizado === 0)
    const repuestosPendientes = await db.repuestos.where('sincronizado').equals(0).toArray();
    const ventasPendientes = await db.ventas.where('sincronizado').equals(0).toArray();
    const logsPendientes = await db.auditoria_logs.where('sincronizado').equals(0).toArray();

    for (let v of ventasPendientes) {
      v.detalles = await db.detalle_ventas.where('venta_id').equals(v.id).toArray();
    }

    // 2. PUSH: Enviar pendientes al servidor
    if (repuestosPendientes.length > 0 || ventasPendientes.length > 0 || logsPendientes.length > 0) {
      const pushRes = await fetch(`${API_URL}/sync/push`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${sesion.access_token}`
        },
        body: JSON.stringify({
          repuestos: repuestosPendientes,
          ventas: ventasPendientes,
          auditoria: logsPendientes
        })
      });

      if (pushRes.ok) {
        // Marcar como sincronizados en el celular
        await db.repuestos.bulkPut(repuestosPendientes.map(r => ({ ...r, sincronizado: 1 })));
        await db.ventas.bulkPut(ventasPendientes.map(v => {
          const { detalles, ...ventaSinDetalles } = v;
          return { ...ventaSinDetalles, sincronizado: 1 };
        }));
        await db.auditoria_logs.bulkPut(logsPendientes.map(l => ({ ...l, sincronizado: 1 })));
      }
    }

    // 3. PULL: Descargar cambios hechos por otros celulares
    const lastSync = localStorage.getItem('last_sync_time');
    const urlPull = lastSync ? `${API_URL}/sync/pull?since=${encodeURIComponent(lastSync)}` : `${API_URL}/sync/pull`;

    const pullRes = await fetch(urlPull, {
      headers: { 'Authorization': `Bearer ${sesion.access_token}` }
    });

    if (pullRes.ok) {
      const data = await pullRes.json();
      if (data.usuarios?.length) await db.usuarios.bulkPut(data.usuarios);
      if (data.repuestos?.length) {
        await db.repuestos.bulkPut(data.repuestos.map(r => ({ ...r, sincronizado: 1 })));
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
    if (btnSync) btnSync.textContent = 'Sincronizar Ahora';
  }
}

window.addEventListener('online', sincronizarDatos);