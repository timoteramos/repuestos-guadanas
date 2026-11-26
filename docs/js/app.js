let carrito = [];

// Registrar Service Worker para que funcione sin internet (Modo Avión)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('./sw.js').catch(console.error);
}

// Actualizar indicador visual de red (En Línea / Sin Internet)
function actualizarEstadoRed() {
  const badge = document.getElementById('net-status');
  if (!badge) return;
  if (navigator.onLine) {
    badge.textContent = 'En línea';
    badge.className = 'badge online';
  } else {
    badge.textContent = 'Sin internet (Offline)';
    badge.className = 'badge offline';
  }
}
window.addEventListener('online', actualizarEstadoRed);
window.addEventListener('offline', actualizarEstadoRed);

// Inicializar aplicación al abrir
window.addEventListener('DOMContentLoaded', async () => {
  actualizarEstadoRed();
  const sesion = getSession();
  if (sesion) {
    iniciarInterfaz(sesion);
    await renderizarTodo();
    if (navigator.onLine) sincronizarDatos();
  }
});

// --- AUTENTICACIÓN ---
async function iniciarSesion(e) {
  e.preventDefault();
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-pass').value;
  const errorEl = document.getElementById('login-error');
  errorEl.textContent = '';

  try {
    const res = await fetch(`${API_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error al iniciar sesión');

    localStorage.setItem('sesion_guadanas', JSON.stringify(data));
    iniciarInterfaz(data);
    await sincronizarDatos();
    await renderizarTodo();
  } catch (err) {
    errorEl.textContent = err.message;
  }
}

function cerrarSesion() {
  if (confirm('¿Seguro que deseas cerrar sesión?')) {
    localStorage.removeItem('sesion_guadanas');
    location.reload();
  }
}

function iniciarInterfaz(sesion) {
  document.getElementById('view-login').classList.add('hidden');
  document.getElementById('app-shell').classList.remove('hidden');
  document.getElementById('user-info').textContent = `${sesion.usuario.nombre} (${sesion.usuario.rol})`;

  // Control de permisos por Rol (ADMIN vs VENDEDOR)
  const soloAdmin = document.querySelectorAll('.admin-only');
  soloAdmin.forEach(el => {
    if (sesion.usuario.rol === 'ADMIN') el.classList.remove('hidden');
    else el.classList.add('hidden');
  });
}

// Navegación entre pestañas
function mostrarTab(tabId, btn) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.add('hidden'));
  document.getElementById(tabId).classList.remove('hidden');
  document.querySelectorAll('.bottom-nav button').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

// --- INVENTARIO DE REPUESTOS ---
async function guardarRepuesto(e) {
  e.preventDefault();
  const sesion = getSession();
  if (sesion.usuario.rol !== 'ADMIN') return alert('Solo el Administrador puede modificar inventario.');

  const id = crypto.randomUUID();
  const ahora = new Date().toISOString();
  const nuevoRepuesto = {
    id,
    codigo: document.getElementById('rep-codigo').value.trim().toUpperCase(),
    nombre: document.getElementById('rep-nombre').value.trim(),
    marca_compatible: document.getElementById('rep-marca').value.trim(),
    categoria: document.getElementById('rep-categoria').value,
    precio_compra: parseFloat(document.getElementById('rep-compra').value) || 0,
    precio_venta: parseFloat(document.getElementById('rep-venta').value),
    stock_actual: parseInt(document.getElementById('rep-stock').value) || 0,
    stock_minimo: parseInt(document.getElementById('rep-minimo').value) || 3,
    updated_at: ahora,
    updated_by: sesion.usuario.id,
    deleted_at: null,
    sincronizado: 0
  };

  await db.repuestos.put(nuevoRepuesto);
  await registrarAuditoria('repuestos', id, 'CREAR_REPUESTO', null, nuevoRepuesto);

  e.target.reset();
  await renderizarTodo();
  sincronizarDatos();
}

async function ajustarStockRapido(id, delta) {
  const sesion = getSession();
  if (sesion.usuario.rol !== 'ADMIN') return;
  const rep = await db.repuestos.get(id);
  if (!rep) return;

  const stockAnterior = rep.stock_actual;
  rep.stock_actual = Math.max(0, rep.stock_actual + delta);
  rep.updated_at = new Date().toISOString();
  rep.updated_by = sesion.usuario.id;
  rep.sincronizado = 0;

  await db.repuestos.put(rep);
  await registrarAuditoria('repuestos', id, 'AJUSTE_STOCK', { stock: stockAnterior }, { stock: rep.stock_actual, nombre: rep.nombre });
  await renderizarTodo();
  sincronizarDatos();
}

// --- PUNTO DE VENTA (POS) ---
function agregarAlCarrito(rep) {
  if (rep.stock_actual <= 0) return alert('Sin existencias disponibles de este repuesto.');
  const existente = carrito.find(i => i.repuesto.id === rep.id);
  if (existente) {
    if (existente.cantidad + 1 > rep.stock_actual) return alert('No hay más unidades en stock.');
    existente.cantidad++;
  } else {
    carrito.push({ repuesto: rep, cantidad: 1 });
  }
  renderizarCarrito();
}

function quitarDelCarrito(index) {
  carrito.splice(index, 1);
  renderizarCarrito();
}

function renderizarCarrito() {
  const cont = document.getElementById('carrito-lista');
  const totalEl = document.getElementById('carrito-total');
  cont.innerHTML = '';
  let total = 0;

  carrito.forEach((item, idx) => {
    const subtotal = item.repuesto.precio_venta * item.cantidad;
    total += subtotal;
    cont.innerHTML += `
      <div class="item-row">
        <div>
          <strong>${item.repuesto.nombre}</strong> (${item.cantidad} x $${item.repuesto.precio_venta.toLocaleString('es-CO')})
        </div>
        <div>
          <strong>$${subtotal.toLocaleString('es-CO')}</strong>
          <button class="small danger" onclick="quitarDelCarrito(${idx})">X</button>
        </div>
      </div>`;
  });
  totalEl.textContent = `$${total.toLocaleString('es-CO')}`;
}

async function confirmarVenta() {
  if (carrito.length === 0) return alert('Agrega al menos un repuesto a la venta.');
  const sesion = getSession();
  const ventaId = crypto.randomUUID();
  const ahora = new Date().toISOString();
  const cliente = document.getElementById('venta-cliente').value.trim() || 'Mostrador';
  const metodo = document.getElementById('venta-metodo').value;

  let total = 0;
  const detalles = [];

  for (const item of carrito) {
    const subtotal = item.repuesto.precio_venta * item.cantidad;
    total += subtotal;

    detalles.push({
      id: crypto.randomUUID(),
      venta_id: ventaId,
      repuesto_id: item.repuesto.id,
      cantidad: item.cantidad,
      precio_unitario: item.repuesto.precio_venta,
      subtotal,
      sincronizado: 0
    });

    // Descontar stock localmente en el celular de inmediato
    const repLocal = await db.repuestos.get(item.repuesto.id);
    if (repLocal) {
      repLocal.stock_actual = Math.max(0, repLocal.stock_actual - item.cantidad);
      repLocal.updated_at = ahora;
      await db.repuestos.put(repLocal);
    }
  }

  const nuevaVenta = {
    id: ventaId,
    usuario_id: sesion.usuario.id,
    cliente_nombre: cliente,
    metodo_pago: metodo,
    total,
    estado: 'COMPLETADA',
    created_at: ahora,
    updated_at: ahora,
    sincronizado: 0
  };

  await db.ventas.put(nuevaVenta);
  await db.detalle_ventas.bulkPut(detalles);
  await registrarAuditoria('ventas', ventaId, 'VENTA_REGISTRADA', null, {
    cliente,
    metodo,
    total,
    items: carrito.map(c => `${c.cantidad}x ${c.repuesto.nombre}`)
  });

  carrito = [];
  document.getElementById('venta-cliente').value = '';
  renderizarCarrito();
  await renderizarTodo();
  alert('Venta guardada exitosamente (funciona sin internet y se respaldará en la nube).');
  sincronizarDatos();
}

// --- CREAR USUARIOS VENDEDORES (SOLO ADMIN) ---
async function crearNuevoUsuario(e) {
  e.preventDefault();
  if (!navigator.onLine) return alert('Necesitas internet para registrar una cuenta nueva en el servidor.');
  const sesion = getSession();

  const body = {
    nombre: document.getElementById('usr-nombre').value.trim(),
    email: document.getElementById('usr-email').value.trim(),
    password: document.getElementById('usr-pass').value,
    rol: document.getElementById('usr-rol').value
  };

  try {
    const res = await fetch(`${API_URL}/usuarios`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${sesion.access_token}`
      },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error al crear usuario');
    alert(data.mensaje);
    e.target.reset();
    sincronizarDatos();
  } catch (err) {
    alert(err.message);
  }
}

// --- RENDERIZADO DE PANTALLAS ---
async function renderizarTodo() {
  const sesion = getSession();
  if (!sesion) return;
  const esAdmin = sesion.usuario.rol === 'ADMIN';
  const filtro = (document.getElementById('buscador-repuestos')?.value || '').toLowerCase();

  const repuestos = await db.repuestos.filter(r => !r.deleted_at).toArray();
  const filtrados = repuestos.filter(r =>
    r.nombre.toLowerCase().includes(filtro) ||
    r.codigo.toLowerCase().includes(filtro) ||
    (r.marca_compatible || '').toLowerCase().includes(filtro)
  );

  // 1. Lista para vender (POS)
  const posLista = document.getElementById('pos-resultados');
  posLista.innerHTML = filtrados.length === 0 ? '<p>No hay repuestos registrados aún.</p>' : '';
  filtrados.forEach(r => {
    const stockClass = r.stock_actual <= r.stock_minimo ? 'stock-low' : '';
    posLista.innerHTML += `
      <div class="item-row">
        <div>
          <strong>${r.nombre}</strong> <small>(${r.codigo})</small><br>
          <small>${r.marca_compatible || 'Universal'} | Stock: <span class="${stockClass}">${r.stock_actual}</span></small>
        </div>
        <div style="text-align:right">
          <div><strong>$${r.precio_venta.toLocaleString('es-CO')}</strong></div>
          <button class="small" onclick='agregarAlCarrito(${JSON.stringify(r)})'>+ Agregar</button>
        </div>
      </div>`;
  });

  // 2. Lista de inventario completo
  const invLista = document.getElementById('inventario-lista');
  invLista.innerHTML = '';
  repuestos.forEach(r => {
    const controlesAdmin = esAdmin ? `
      <div style="margin-top:6px;">
        <button class="small secondary" onclick="ajustarStockRapido('${r.id}', -1)">-1</button>
        <button class="small" onclick="ajustarStockRapido('${r.id}', 1)">+1</button>
      </div>` : '';

    invLista.innerHTML += `
      <div class="item-row">
        <div>
          <strong>[${r.codigo}] ${r.nombre}</strong><br>
          <small>Compatibilidad: ${r.marca_compatible || 'N/A'} | Cat: ${r.categoria}</small><br>
          <small>Venta: $${r.precio_venta.toLocaleString('es-CO')} ${esAdmin ? `| Costo: $${r.precio_compra.toLocaleString('es-CO')}` : ''}</small>
        </div>
        <div style="text-align:right">
          <span class="${r.stock_actual <= r.stock_minimo ? 'stock-low' : ''}">Stock: ${r.stock_actual}</span>
          ${controlesAdmin}
        </div>
      </div>`;
  });

  // 3. Historial de Auditoría (Solo ADMIN)
  if (esAdmin) {
    const usuarios = await db.usuarios.toArray();
    const mapaUsuarios = Object.fromEntries(usuarios.map(u => [u.id, u.nombre]));
    const logs = await db.auditoria_logs.orderBy('fecha_hora').reverse().limit(50).toArray();
    const auditCont = document.getElementById('auditoria-lista');
    auditCont.innerHTML = logs.length === 0 ? '<p>Sin movimientos registrados.</p>' : '';

    logs.forEach(l => {
      const autor = mapaUsuarios[l.usuario_id] || sesion.usuario.nombre;
      const fecha = new Date(l.fecha_hora).toLocaleString('es-CO');
      auditCont.innerHTML += `
        <div class="log-entry">
          <strong>${l.accion}</strong> — por <strong>${autor}</strong><br>
          <small>${fecha} ${l.sincronizado ? '☁️ Respaldado' : '📱 Pendiente subir'}</small><br>
          <code>${JSON.stringify(l.detalle_nuevo || {})}</code>
        </div>`;
    });
  }
}