let carrito = [];
let filtroInventarioActual = 'todos';
let tablaVentasActual = 'ventas';
let fotoBase64Actual = null;
let productoExistenteDetectado = null;

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('./sw.js').catch(console.error);
}

function actualizarEstadoRed() {
  const badge = document.getElementById('net-status');
  if (!badge) return;
  if (navigator.onLine) {
    badge.textContent = 'En línea';
    badge.className = 'badge online';
  } else {
    badge.textContent = 'Sin internet';
    badge.className = 'badge offline';
  }
}
window.addEventListener('online', actualizarEstadoRed);
window.addEventListener('offline', actualizarEstadoRed);

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

  const soloAdmin = document.querySelectorAll('.admin-only');
  soloAdmin.forEach(el => {
    if (sesion.usuario.rol === 'ADMIN') el.classList.remove('hidden');
    else el.classList.add('hidden');
  });

  // Asegurar que el formulario de producto siempre arranque oculto al entrar
  const cardReg = document.getElementById('card-registro-producto');
  if (cardReg) cardReg.classList.add('hidden');
}

function mostrarTab(tabId, btn) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.add('hidden'));
  document.getElementById(tabId).classList.remove('hidden');
  document.querySelectorAll('.bottom-nav button').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  if (tabId === 'tab-inventario') {
    cerrarFormularioProducto();
  }
}

// --- FORMULARIO DESPLEGABLE, EDITAR, ELIMINAR Y AUTO-RELLENO ---
function abrirNuevoProducto() {
  document.getElementById('form-producto').reset();
  document.getElementById('rep-edit-id').value = '';
  document.getElementById('titulo-form-producto').textContent = 'Registrar Producto';
  document.getElementById('btn-submit-producto').textContent = 'Guardar en Inventario';
  document.getElementById('rep-stock').placeholder = 'Cantidad';
  productoExistenteDetectado = null;
  document.getElementById('aviso-producto-existente').classList.add('hidden');
  limpiarFoto();

  document.getElementById('card-registro-producto').classList.remove('hidden');
  document.getElementById('btn-toggle-registro').classList.add('hidden');
}

function cerrarFormularioProducto() {
  const card = document.getElementById('card-registro-producto');
  const btn = document.getElementById('btn-toggle-registro');
  if (card) card.classList.add('hidden');
  if (btn && getSession()?.usuario?.rol === 'ADMIN') btn.classList.remove('hidden');
  document.getElementById('rep-edit-id').value = '';
  productoExistenteDetectado = null;
  document.getElementById('aviso-producto-existente').classList.add('hidden');
  limpiarFoto();
}

async function editarProductoInventario(id) {
  const sesion = getSession();
  if (sesion.usuario.rol !== 'ADMIN') return;

  const p = await db.repuestos.get(id);
  if (!p) return;

  document.getElementById('rep-edit-id').value = p.id;
  document.getElementById('rep-codigo').value = p.codigo;
  document.getElementById('rep-nombre').value = p.nombre;
  document.getElementById('rep-marca').value = p.marca_compatible || '';
  document.getElementById('rep-compra').value = p.precio_compra || 0;
  document.getElementById('rep-venta').value = p.precio_venta || 0;
  document.getElementById('rep-stock').value = Math.max(0, p.stock_actual);

  document.getElementById('titulo-form-producto').textContent = 'Modificar Producto';
  document.getElementById('btn-submit-producto').textContent = 'Actualizar Producto';
  document.getElementById('aviso-producto-existente').classList.add('hidden');

  if (p.foto) {
    fotoBase64Actual = p.foto;
    document.getElementById('preview-foto-img').src = p.foto;
    document.getElementById('preview-foto-box').classList.remove('hidden');
  } else {
    limpiarFoto();
  }

  document.getElementById('card-registro-producto').classList.remove('hidden');
  document.getElementById('btn-toggle-registro').classList.add('hidden');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function eliminarProductoDefinitivo(id) {
  const sesion = getSession();
  if (sesion.usuario.rol !== 'ADMIN') return;

  const p = await db.repuestos.get(id);
  if (!p) return;

  if (!confirm(`¿Eliminar por completo "${p.nombre}" (${p.codigo}) del inventario? Esta acción no se puede deshacer.`)) {
    return;
  }

  await db.repuestos.delete(id);
  await db.repuestos_eliminados.put({ id });
  await registrarAuditoria('repuestos', id, 'ELIMINAR_PRODUCTO', null, {
    codigo: p.codigo,
    nombre: p.nombre
  });

  await renderizarTodo();
  sincronizarDatos();
}

async function verificarCodigoExistente() {
  // Si está editando un producto existente, no mostrar el aviso de duplicado
  if (document.getElementById('rep-edit-id').value) return;

  const codigo = document.getElementById('rep-codigo').value.trim().toUpperCase();
  const aviso = document.getElementById('aviso-producto-existente');
  const texto = document.getElementById('texto-producto-existente');

  if (!codigo) {
    productoExistenteDetectado = null;
    aviso.classList.add('hidden');
    return;
  }

  const encontrado = await db.repuestos.where('codigo').equals(codigo).first();
  if (encontrado && !encontrado.deleted_at) {
    productoExistenteDetectado = encontrado;
    texto.innerHTML = `Este código ya pertenece a <strong>${encontrado.nombre}</strong> (Cantidad actual: <strong>${Math.max(0, encontrado.stock_actual)}</strong>).`;
    aviso.classList.remove('hidden');
  } else {
    productoExistenteDetectado = null;
    aviso.classList.add('hidden');
  }
}

function rellenarProductoExistente() {
  if (!productoExistenteDetectado) return;
  const p = productoExistenteDetectado;

  document.getElementById('rep-nombre').value = p.nombre || '';
  document.getElementById('rep-marca').value = p.marca_compatible || '';
  document.getElementById('rep-compra').value = p.precio_compra || 0;
  document.getElementById('rep-venta').value = p.precio_venta || 0;
  document.getElementById('rep-stock').value = '';
  document.getElementById('rep-stock').placeholder = `Cantidad nueva a sumar (actual: ${Math.max(0, p.stock_actual)})`;
  document.getElementById('rep-stock').focus();

  if (p.foto) {
    fotoBase64Actual = p.foto;
    document.getElementById('preview-foto-img').src = p.foto;
    document.getElementById('preview-foto-box').classList.remove('hidden');
  }
}

function previsualizarFoto(event) {
  const archivo = event.target.files[0];
  if (!archivo) return;

  const lector = new FileReader();
  lector.onload = (e) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      const maxAncho = 360;
      const escala = Math.min(1, maxAncho / img.width);
      canvas.width = img.width * escala;
      canvas.height = img.height * escala;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

      fotoBase64Actual = canvas.toDataURL('image/jpeg', 0.65);
      document.getElementById('preview-foto-img').src = fotoBase64Actual;
      document.getElementById('preview-foto-box').classList.remove('hidden');
    };
    img.src = e.target.result;
  };
  lector.readAsDataURL(archivo);
}

function limpiarFoto() {
  fotoBase64Actual = null;
  const inputFoto = document.getElementById('rep-foto');
  if (inputFoto) inputFoto.value = '';
  const box = document.getElementById('preview-foto-box');
  if (box) box.classList.add('hidden');
}

function cambiarFiltroInventario(filtro, btn) {
  filtroInventarioActual = filtro;
  document.querySelectorAll('#tab-inventario .filtros-bar button').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderizarTodo();
}

// --- INVENTARIO DE REPUESTOS ---
async function guardarRepuesto(e) {
  e.preventDefault();
  const sesion = getSession();
  if (sesion.usuario.rol !== 'ADMIN') return alert('Solo el Administrador puede modificar inventario.');

  const ahora = new Date().toISOString();
  const editId = document.getElementById('rep-edit-id').value;
  const codigo = document.getElementById('rep-codigo').value.trim().toUpperCase();
  const cantidadForm = Math.max(0, parseInt(document.getElementById('rep-stock').value) || 0);

  let id;
  let cantidadFinal;
  let accionLog;
  let fechaCreacion = ahora;

  if (editId) {
    // Modo Modificar Producto existente
    const prev = await db.repuestos.get(editId);
    id = editId;
    cantidadFinal = cantidadForm;
    fechaCreacion = prev?.created_at || ahora;
    accionLog = 'MODIFICAR_PRODUCTO';
  } else {
    // Modo Nuevo Registro o Reingreso de código existente
    const existente = productoExistenteDetectado || await db.repuestos.where('codigo').equals(codigo).first();
    id = existente ? existente.id : crypto.randomUUID();
    cantidadFinal = existente ? Math.max(0, existente.stock_actual + cantidadForm) : cantidadForm;
    accionLog = existente ? 'REINGRESO_PRODUCTO' : 'CREAR_REPUESTO';
  }

  const repuestoFinal = {
    id,
    codigo,
    nombre: document.getElementById('rep-nombre').value.trim(),
    marca_compatible: document.getElementById('rep-marca').value.trim(),
    categoria: 'General',
    foto: fotoBase64Actual,
    precio_compra: parseFloat(document.getElementById('rep-compra').value) || 0,
    precio_venta: parseFloat(document.getElementById('rep-venta').value),
    stock_actual: Math.max(0, cantidadFinal),
    stock_minimo: 3, // Alerta mínima fija en el sistema (3 unidades)
    created_at: fechaCreacion,
    updated_at: ahora,
    updated_by: sesion.usuario.id,
    deleted_at: null,
    sincronizado: 0
  };

  await db.repuestos.put(repuestoFinal);
  await registrarAuditoria('repuestos', id, accionLog, null, {
    codigo: repuestoFinal.codigo,
    nombre: repuestoFinal.nombre,
    cantidad: repuestoFinal.stock_actual,
    venta: repuestoFinal.precio_venta
  });

  e.target.reset();
  cerrarFormularioProducto();
  await renderizarTodo();
  sincronizarDatos();
}

async function ajustarStockRapido(id, delta) {
  const sesion = getSession();
  if (sesion.usuario.rol !== 'ADMIN') return;
  const rep = await db.repuestos.get(id);
  if (!rep) return;

  const cantidadAnterior = Math.max(0, rep.stock_actual);
  rep.stock_actual = Math.max(0, cantidadAnterior + delta);
  rep.updated_at = new Date().toISOString();
  if (delta > 0) rep.created_at = rep.updated_at;
  rep.updated_by = sesion.usuario.id;
  rep.sincronizado = 0;

  await db.repuestos.put(rep);
  await registrarAuditoria('repuestos', id, 'AJUSTE_CANTIDAD', { cantidad: cantidadAnterior }, { cantidad: rep.stock_actual, nombre: rep.nombre });
  await renderizarTodo();
  sincronizarDatos();
}

// --- GASTOS DEL LOCAL ---
async function guardarGasto(e) {
  e.preventDefault();
  const sesion = getSession();
  if (!sesion) return;

  const id = crypto.randomUUID();
  const ahora = new Date().toISOString();
  const nuevoGasto = {
    id,
    usuario_id: sesion.usuario.id,
    categoria: document.getElementById('gasto-categoria').value,
    descripcion: document.getElementById('gasto-descripcion').value.trim(),
    monto: parseFloat(document.getElementById('gasto-monto').value) || 0,
    metodo_pago: document.getElementById('gasto-metodo').value,
    created_at: ahora,
    updated_at: ahora,
    sincronizado: 0
  };

  await db.gastos.put(nuevoGasto);
  await registrarAuditoria('gastos', id, 'GASTO_REGISTRADO', null, {
    categoria: nuevoGasto.categoria,
    descripcion: nuevoGasto.descripcion,
    monto: nuevoGasto.monto,
    metodo: nuevoGasto.metodo_pago
  });

  e.target.reset();
  await renderizarTodo();
  alert('Gasto guardado correctamente.');
  sincronizarDatos();
}

// --- PUNTO DE VENTA (POS) Y TABLA DE VENTAS / FIADOS ---
function agregarAlCarrito(rep) {
  if (rep.stock_actual <= 0) return alert('Sin cantidad disponible de este producto.');
  const existente = carrito.find(i => i.repuesto.id === rep.id);
  if (existente) {
    if (existente.cantidad + 1 > rep.stock_actual) return alert('No hay más unidades disponibles.');
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
  if (carrito.length === 0) return alert('Agrega al menos un producto a la venta.');
  const sesion = getSession();
  const ventaId = crypto.randomUUID();
  const ahora = new Date().toISOString();
  const metodo = document.getElementById('venta-metodo').value;
  const clienteInput = document.getElementById('venta-cliente').value.trim();

  if ((metodo === 'Fiado' || metodo === 'Credito') && !clienteInput) {
    return alert('Escribe el nombre del cliente cuando la venta es Fiado o Crédito.');
  }

  const cliente = clienteInput || 'Mostrador';
  const estadoVenta = metodo === 'Fiado' ? 'FIADO_PENDIENTE' : 'COMPLETADA';

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
    estado: estadoVenta,
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
  alert('Venta guardada exitosamente.');
  sincronizarDatos();
}

function cambiarTablaVentas(tipo, btn) {
  tablaVentasActual = tipo;
  document.querySelectorAll('#tab-ventas .filtros-bar button').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderizarTablaVentasYFiados();
}

async function marcarFiadoPagado(ventaId) {
  const venta = await db.ventas.get(ventaId);
  if (!venta) return;
  if (!confirm(`¿Confirmar que ${venta.cliente_nombre} pagó $${Number(venta.total).toLocaleString('es-CO')}?`)) return;

  venta.estado = 'COMPLETADA';
  venta.updated_at = new Date().toISOString();
  venta.sincronizado = 0;

  await db.ventas.put(venta);
  await registrarAuditoria('ventas', ventaId, 'FIADO_PAGADO', null, {
    cliente: venta.cliente_nombre,
    total: venta.total,
    metodo: 'Fiado Pagado'
  });

  await renderizarTodo();
  sincronizarDatos();
}

async function renderizarTablaVentasYFiados() {
  const cont = document.getElementById('contenedor-tabla-ventas');
  if (!cont) return;

  const ventas = await db.ventas.orderBy('created_at').reverse().toArray();
  const detalles = await db.detalle_ventas.toArray();
  const repuestos = await db.repuestos.toArray();

  const mapaRep = Object.fromEntries(repuestos.map(r => [r.id, r.nombre]));
  const detallesPorVenta = {};
  detalles.forEach(d => {
    if (!detallesPorVenta[d.venta_id]) detallesPorVenta[d.venta_id] = [];
    const nombreProd = mapaRep[d.repuesto_id] || 'Producto';
    detallesPorVenta[d.venta_id].push(`${d.cantidad}x ${nombreProd}`);
  });

  if (tablaVentasActual === 'fiados') {
    const fiados = ventas.filter(v => v.metodo_pago === 'Fiado');
    if (fiados.length === 0) {
      cont.innerHTML = '<p style="color:var(--muted);">No hay clientes fiados registrados.</p>';
      return;
    }

    let html = `
      <table class="pos-table">
        <thead>
          <tr>
            <th>Cliente</th>
            <th>Detalle</th>
            <th>Valor</th>
            <th>Estado</th>
          </tr>
        </thead>
        <tbody>`;

    fiados.forEach(f => {
      const fecha = new Date(f.created_at).toLocaleDateString('es-CO');
      const itemsTxt = (detallesPorVenta[f.id] || []).join(', ') || 'Venta';
      const pendiente = f.estado === 'FIADO_PENDIENTE';
      const accionHtml = pendiente
        ? `<button class="small" onclick="marcarFiadoPagado('${f.id}')">Cobrar</button>`
        : `<span style="color:var(--primary); font-weight:bold;">Pagado</span>`;

      html += `
        <tr>
          <td><strong>${f.cliente_nombre}</strong><br><small style="color:var(--muted);">${fecha}</small></td>
          <td>${itemsTxt}</td>
          <td><strong>$${Number(f.total).toLocaleString('es-CO')}</strong></td>
          <td>${accionHtml}</td>
        </tr>`;
    });

    html += '</tbody></table>';
    cont.innerHTML = html;
  } else {
    if (ventas.length === 0) {
      cont.innerHTML = '<p style="color:var(--muted);">No hay ventas registradas aún.</p>';
      return;
    }

    let html = `
      <table class="pos-table">
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Cliente / Productos</th>
            <th>Pago</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>`;

    ventas.forEach(v => {
      const fecha = new Date(v.created_at).toLocaleString('es-CO', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
      });
      const itemsTxt = (detallesPorVenta[v.id] || []).join(', ') || 'Venta';
      html += `
        <tr>
          <td>${fecha}</td>
          <td><strong>${v.cliente_nombre}</strong><br><small>${itemsTxt}</small></td>
          <td>${v.metodo_pago}</td>
          <td><strong>$${Number(v.total).toLocaleString('es-CO')}</strong></td>
        </tr>`;
    });

    html += '</tbody></table>';
    cont.innerHTML = html;
  }
}

// --- EXPORTAR A EXCEL (.XLSX) ---
async function exportarExcelCompleto() {
  if (typeof XLSX === 'undefined') {
    return alert('La librería de Excel aún está cargando. Verifica tu conexión la primera vez.');
  }

  const repuestos = await db.repuestos.toArray();
  const ventas = await db.ventas.orderBy('created_at').reverse().toArray();
  const detalles = await db.detalle_ventas.toArray();
  const gastos = await db.gastos.orderBy('created_at').reverse().toArray();
  const usuarios = await db.usuarios.toArray();

  const mapaUsuarios = Object.fromEntries(usuarios.map(u => [u.id, u.nombre]));
  const mapaRep = Object.fromEntries(repuestos.map(r => [r.id, r.nombre]));
  const detallesPorVenta = {};
  detalles.forEach(d => {
    if (!detallesPorVenta[d.venta_id]) detallesPorVenta[d.venta_id] = [];
    detallesPorVenta[d.venta_id].push(`${d.cantidad}x ${mapaRep[d.repuesto_id] || 'Producto'}`);
  });

  // Hoja 1: Inventario
  const hojaInventario = repuestos.map(r => ({
    'Código': r.codigo,
    'Producto': r.nombre,
    'Compatibilidad': r.marca_compatible || 'Universal',
    'Cantidad Actual': Math.max(0, r.stock_actual),
    'Precio Compra ($)': Number(r.precio_compra || 0),
    'Precio Venta ($)': Number(r.precio_venta || 0),
    'Valor Inventario Venta ($)': Math.max(0, r.stock_actual) * Number(r.precio_venta || 0),
    'Fecha Ingreso': new Date(r.created_at || r.updated_at).toLocaleString('es-CO')
  }));

  // Hoja 2: Ventas
  const hojaVentas = ventas.map(v => ({
    'Fecha y Hora': new Date(v.created_at).toLocaleString('es-CO'),
    'Cliente': v.cliente_nombre,
    'Productos': (detallesPorVenta[v.id] || []).join(', '),
    'Medio de Pago': v.metodo_pago,
    'Estado': v.estado,
    'Total ($)': Number(v.total || 0),
    'Vendedor': mapaUsuarios[v.usuario_id] || 'Sistema'
  }));

  // Hoja 3: Clientes Fiados
  const hojaFiados = ventas
    .filter(v => v.metodo_pago === 'Fiado')
    .map(f => ({
      'Fecha': new Date(f.created_at).toLocaleString('es-CO'),
      'Cliente': f.cliente_nombre,
      'Productos': (detallesPorVenta[f.id] || []).join(', '),
      'Valor ($)': Number(f.total || 0),
      'Estado': f.estado === 'FIADO_PENDIENTE' ? 'Pendiente por cobrar' : 'Pagado'
    }));

  // Hoja 4: Gastos
  const hojaGastos = gastos.map(g => ({
    'Fecha': new Date(g.created_at).toLocaleString('es-CO'),
    'Categoría': g.categoria,
    'Descripción': g.descripcion,
    'Método Pago': g.metodo_pago,
    'Monto ($)': Number(g.monto || 0)
  }));

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(hojaInventario.length ? hojaInventario : [{ Mensaje: 'Sin datos' }]), 'Inventario');
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(hojaVentas.length ? hojaVentas : [{ Mensaje: 'Sin datos' }]), 'Ventas');
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(hojaFiados.length ? hojaFiados : [{ Mensaje: 'Sin datos' }]), 'Fiados');
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(hojaGastos.length ? hojaGastos : [{ Mensaje: 'Sin datos' }]), 'Gastos');

  const fechaHoy = new Date().toISOString().slice(0, 10);
  XLSX.writeFile(wb, `Reporte_Guadanas_POS_${fechaHoy}.xlsx`);
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

function formatearDetalleLog(accion, det) {
  if (!det) return '';
  if (typeof det === 'string') return det;

  if (accion === 'CREAR_REPUESTO' || accion === 'REINGRESO_PRODUCTO' || accion === 'MODIFICAR_PRODUCTO') {
    const cant = det.cantidad ?? det.stock ?? det.stock_actual ?? 0;
    const venta = det.venta ?? det.precio_venta ?? 0;
    return `${det.nombre || ''} (Cód: ${det.codigo || '-'}) • Cantidad: ${cant} • Venta: $${Number(venta).toLocaleString('es-CO')}`;
  }
  if (accion === 'ELIMINAR_PRODUCTO') {
    return `Eliminado: ${det.nombre || ''} (Cód: ${det.codigo || '-'})`;
  }
  if (accion === 'AJUSTE_CANTIDAD' || accion === 'AJUSTE_STOCK') {
    const cant = det.cantidad ?? det.stock ?? 0;
    return `${det.nombre || 'Producto'} • Nueva cantidad: ${cant}`;
  }
  if (accion === 'VENTA_REGISTRADA' || accion === 'FIADO_PAGADO') {
    const items = Array.isArray(det.items) ? det.items.join(', ') : '';
    return `${items ? items + ' • ' : ''}Total: $${Number(det.total || 0).toLocaleString('es-CO')} (${det.metodo || ''}) • Cliente: ${det.cliente || 'Mostrador'}`;
  }
  if (accion === 'GASTO_REGISTRADO') {
    return `${det.categoria || ''}: ${det.descripcion || ''} • -$${Number(det.monto || 0).toLocaleString('es-CO')} (${det.metodo || ''})`;
  }
  return JSON.stringify(det);
}

// --- RENDERIZADO DE PANTALLAS ---
async function renderizarTodo() {
  const sesion = getSession();
  if (!sesion) return;
  const esAdmin = sesion.usuario.rol === 'ADMIN';
  const filtroPos = (document.getElementById('buscador-repuestos')?.value || '').toLowerCase();
  const filtroInv = (document.getElementById('buscador-inventario')?.value || '').toLowerCase();

  const repuestos = await db.repuestos.filter(r => !r.deleted_at).toArray();

  // 1. Lista para VENDER (Oculta productos con cantidad 0)
  const disponiblesPOS = repuestos.filter(r =>
    r.stock_actual > 0 &&
    (
      r.nombre.toLowerCase().includes(filtroPos) ||
      r.codigo.toLowerCase().includes(filtroPos) ||
      (r.marca_compatible || '').toLowerCase().includes(filtroPos)
    )
  );

  const posLista = document.getElementById('pos-resultados');
  posLista.innerHTML = disponiblesPOS.length === 0 ? '<p style="color:var(--muted);">No hay productos disponibles con cantidad mayor a 0.</p>' : '';
  disponiblesPOS.forEach(r => {
    const cantClass = r.stock_actual <= r.stock_minimo ? 'stock-low' : '';
    const imgHtml = r.foto ? `<img src="${r.foto}" class="prod-thumb" alt="${r.nombre}">` : '';
    posLista.innerHTML += `
      <div class="item-row">
        <div class="item-info">
          ${imgHtml}
          <div>
            <strong>${r.nombre}</strong> <small>(${r.codigo})</small><br>
            <small>${r.marca_compatible || 'Universal'} | Cantidad: <span class="${cantClass}">${r.stock_actual}</span></small>
          </div>
        </div>
        <div style="text-align:right; flex-shrink:0;">
          <div><strong>$${r.precio_venta.toLocaleString('es-CO')}</strong></div>
          <button class="small" onclick='agregarAlCarrito(${JSON.stringify({ id: r.id, nombre: r.nombre, precio_venta: r.precio_venta, stock_actual: r.stock_actual })})'>+ Agregar</button>
        </div>
      </div>`;
  });

  // 2. Tablas de Ventas y Fiados
  await renderizarTablaVentasYFiados();

  // 3. Lista de INVENTARIO (Filtros + Editar + Eliminar + Cantidad + Fecha + Foto)
  const invFiltrados = repuestos.filter(r => {
    const coincideTexto =
      r.nombre.toLowerCase().includes(filtroInv) ||
      r.codigo.toLowerCase().includes(filtroInv) ||
      (r.marca_compatible || '').toLowerCase().includes(filtroInv);
    if (!coincideTexto) return false;
    if (filtroInventarioActual === 'disponibles') return r.stock_actual > 0;
    if (filtroInventarioActual === 'agotados') return r.stock_actual <= 0;
    return true;
  });

  const invLista = document.getElementById('inventario-lista');
  invLista.innerHTML = invFiltrados.length === 0 ? '<p style="color:var(--muted);">No hay productos en esta vista.</p>' : '';
  invFiltrados.forEach(r => {
    const cantSegura = Math.max(0, r.stock_actual);
    const fechaIngreso = new Date(r.created_at || r.updated_at).toLocaleDateString('es-CO');
    const imgHtml = r.foto ? `<img src="${r.foto}" class="prod-thumb" alt="${r.nombre}">` : '';
    const controlesAdmin = esAdmin ? `
      <div style="display:flex; gap:4px; justify-content:flex-end; margin-top:6px; flex-wrap:wrap;">
        <button class="small secondary" onclick="ajustarStockRapido('${r.id}', -1)">-1</button>
        <button class="small" onclick="ajustarStockRapido('${r.id}', 1)">+1</button>
        <button class="small warning" onclick="editarProductoInventario('${r.id}')">Editar</button>
        <button class="small danger" onclick="eliminarProductoDefinitivo('${r.id}')">Eliminar</button>
      </div>` : '';

    invLista.innerHTML += `
      <div class="item-row">
        <div class="item-info">
          ${imgHtml}
          <div>
            <strong>[${r.codigo}] ${r.nombre}</strong><br>
            <small>Compatibilidad: ${r.marca_compatible || 'N/A'}</small><br>
            <small>Venta: $${r.precio_venta.toLocaleString('es-CO')} ${esAdmin ? `| Costo: $${r.precio_compra.toLocaleString('es-CO')}` : ''}</small><br>
            <small style="color:var(--muted);">Ingreso: ${fechaIngreso}</small>
          </div>
        </div>
        <div style="text-align:right; flex-shrink:0;">
          <span class="${cantSegura <= r.stock_minimo ? 'stock-low' : ''}">Cantidad: ${cantSegura}</span>
          ${controlesAdmin}
        </div>
      </div>`;
  });

  // 4. Lista de Gastos del Local
  const gastos = await db.gastos.orderBy('created_at').reverse().toArray();
  const gastosLista = document.getElementById('gastos-lista');
  const totalGastosEl = document.getElementById('total-gastos-texto');
  let totalGastos = 0;

  if (gastosLista) {
    gastosLista.innerHTML = gastos.length === 0 ? '<p style="color:var(--muted);">No hay gastos registrados aún.</p>' : '';
    gastos.forEach(g => {
      totalGastos += Number(g.monto || 0);
      const fecha = new Date(g.created_at).toLocaleString('es-CO');
      gastosLista.innerHTML += `
        <div class="gasto-entry">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <strong>${g.categoria}</strong>
            <strong style="color:var(--danger);">-$${Number(g.monto).toLocaleString('es-CO')}</strong>
          </div>
          <div class="log-detalle">${g.descripcion} (${g.metodo_pago})</div>
          <small style="color:var(--muted);">${fecha} • ${g.sincronizado ? '☁️ Respaldado' : '📱 Pendiente subir'}</small>
        </div>`;
    });
  }
  if (totalGastosEl) totalGastosEl.textContent = `Total: $${totalGastos.toLocaleString('es-CO')}`;

  // 5. Historial de Auditoría (Solo ADMIN)
  if (esAdmin) {
    const usuarios = await db.usuarios.toArray();
    const mapaUsuarios = Object.fromEntries(usuarios.map(u => [u.id, u.nombre]));
    const logs = await db.auditoria_logs.orderBy('fecha_hora').reverse().limit(50).toArray();
    const auditCont = document.getElementById('auditoria-lista');
    auditCont.innerHTML = logs.length === 0 ? '<p>Sin movimientos registrados.</p>' : '';

    logs.forEach(l => {
      const autor = mapaUsuarios[l.usuario_id] || sesion.usuario.nombre;
      const fecha = new Date(l.fecha_hora).toLocaleString('es-CO');
      const resumen = formatearDetalleLog(l.accion, l.detalle_nuevo);
      auditCont.innerHTML += `
        <div class="log-entry">
          <strong>${l.accion}</strong> — por <strong>${autor}</strong><br>
          <small>${fecha} ${l.sincronizado ? '☁️ Respaldado' : '📱 Pendiente subir'}</small>
          <div class="log-detalle">${resumen}</div>
        </div>`;
    });
  }
}