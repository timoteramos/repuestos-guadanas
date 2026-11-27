import uuid
from datetime import datetime, timezone
from typing import List, Optional, Any
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db, engine
from models import Base, Usuario, Repuesto, Venta, DetalleVenta, AuditoriaLog, Gasto
from auth import hash_password, verify_password, create_access_token, get_current_user, require_admin

Base.metadata.create_all(bind=engine)

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE repuestos ADD COLUMN IF NOT EXISTS foto TEXT"))
    conn.execute(text("ALTER TABLE repuestos ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ"))

app = FastAPI(title="API Repuestos de Guadañas Offline-First")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ESQUEMAS DE DATOS ---
class LoginRequest(BaseModel):
    email: str
    password: str

class CrearUsuarioRequest(BaseModel):
    nombre: str
    email: str
    password: str
    rol: str

class RepuestoSync(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    marca_compatible: Optional[str] = ""
    categoria: Optional[str] = "General"
    foto: Optional[str] = None
    precio_compra: float
    precio_venta: float
    stock_actual: int
    stock_minimo: int = 3
    created_at: Optional[datetime] = None
    updated_at: datetime
    updated_by: Optional[uuid.UUID] = None
    deleted_at: Optional[datetime] = None

class DetalleVentaSync(BaseModel):
    id: uuid.UUID
    venta_id: uuid.UUID
    repuesto_id: uuid.UUID
    cantidad: int
    precio_unitario: float
    subtotal: float

class VentaSync(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID
    cliente_nombre: str = "Mostrador"
    metodo_pago: str
    total: float
    estado: str = "COMPLETADA"
    created_at: datetime
    updated_at: datetime
    detalles: List[DetalleVentaSync] = []

class GastoSync(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID
    categoria: str
    descripcion: str
    monto: float
    metodo_pago: str = "Efectivo"
    created_at: datetime
    updated_at: datetime

class AuditoriaSync(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID
    tabla_afectada: str
    registro_id: uuid.UUID
    accion: str
    detalle_anterior: Optional[Any] = None
    detalle_nuevo: Optional[Any] = None
    fecha_hora: datetime

class PushPayload(BaseModel):
    repuestos: List[RepuestoSync] = []
    eliminados: List[uuid.UUID] = []
    ventas: List[VentaSync] = []
    gastos: List[GastoSync] = []
    auditoria: List[AuditoriaSync] = []

# --- ENDPOINTS DE AUTENTICACIÓN ---

@app.post("/setup-admin")
def crear_primer_admin(data: CrearUsuarioRequest, db: Session = Depends(get_db)):
    existe = db.query(Usuario).first()
    if existe:
        raise HTTPException(status_code=400, detail="Ya existe un administrador. Inicia sesión para crear más usuarios.")
    
    nuevo = Usuario(
        id=uuid.uuid4(),
        nombre=data.nombre,
        email=data.email.lower().strip(),
        password_hash=hash_password(data.password),
        rol="ADMIN",
        activo=True,
        updated_at=datetime.now(timezone.utc)
    )
    db.add(nuevo)
    db.commit()
    return {"mensaje": "Administrador principal creado con éxito", "id": str(nuevo.id)}

@app.post("/auth/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(Usuario).filter(Usuario.email == data.email.lower().strip()).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")
    if not user.activo:
        raise HTTPException(status_code=403, detail="Usuario desactivado")

    token = create_access_token({"sub": str(user.id), "rol": user.rol})
    return {
        "access_token": token,
        "usuario": {
            "id": str(user.id),
            "nombre": user.nombre,
            "email": user.email,
            "rol": user.rol
        }
    }

@app.post("/usuarios", dependencies=[Depends(require_admin)])
def crear_usuario(data: CrearUsuarioRequest, db: Session = Depends(get_db)):
    if data.rol not in ["ADMIN", "VENDEDOR"]:
        raise HTTPException(status_code=400, detail="Rol debe ser ADMIN o VENDEDOR")
    if db.query(Usuario).filter(Usuario.email == data.email.lower().strip()).first():
        raise HTTPException(status_code=400, detail="Ese correo ya está registrado")

    nuevo = Usuario(
        id=uuid.uuid4(),
        nombre=data.nombre,
        email=data.email.lower().strip(),
        password_hash=hash_password(data.password),
        rol=data.rol,
        activo=True,
        updated_at=datetime.now(timezone.utc)
    )
    db.add(nuevo)
    db.commit()
    return {"mensaje": f"Usuario {nuevo.nombre} creado", "id": str(nuevo.id)}

# --- ENDPOINTS DE SINCRONIZACIÓN OFFLINE-FIRST ---

@app.post("/sync/push")
def sync_push(payload: PushPayload, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    # 1. Eliminar definitivamente productos borrados por ADMIN
    if current_user.rol == "ADMIN":
        for del_id in payload.eliminados:
            rep_borrar = db.query(Repuesto).filter(Repuesto.id == del_id).first()
            if rep_borrar:
                db.query(DetalleVenta).filter(DetalleVenta.repuesto_id == del_id).delete()
                db.delete(rep_borrar)

        # 2. Crear o modificar productos en catálogo (nunca permitir stock negativo)
        for item in payload.repuestos:
            existente = db.query(Repuesto).filter(Repuesto.id == item.id).first()
            if not existente:
                existente = db.query(Repuesto).filter(Repuesto.codigo == item.codigo).first()

            datos = item.model_dump()
            datos["stock_actual"] = max(0, int(datos["stock_actual"]))

            if not existente:
                if not datos.get("created_at"):
                    datos["created_at"] = item.updated_at
                db.add(Repuesto(**datos))
            else:
                if item.updated_at >= existente.updated_at:
                    for key, value in datos.items():
                        if key != "id":
                            setattr(existente, key, value)

    # 3. Sincronizar Ventas nuevas y actualizar estado de fiados pagados
    for v in payload.ventas:
        venta_existente = db.query(Venta).filter(Venta.id == v.id).first()
        if not venta_existente:
            nueva_venta = Venta(
                id=v.id,
                usuario_id=v.usuario_id,
                cliente_nombre=v.cliente_nombre,
                metodo_pago=v.metodo_pago,
                total=v.total,
                estado=v.estado,
                created_at=v.created_at,
                updated_at=v.updated_at
            )
            db.add(nueva_venta)
            db.flush()

            for det in v.detalles:
                db.add(DetalleVenta(**det.model_dump()))
                rep = db.query(Repuesto).filter(Repuesto.id == det.repuesto_id).first()
                if rep:
                    # Nunca baja de 0 aunque varios celulares vendan sin internet a la vez
                    rep.stock_actual = max(0, rep.stock_actual - det.cantidad)
                    rep.updated_at = datetime.now(timezone.utc)
        else:
            if v.updated_at > venta_existente.updated_at:
                venta_existente.estado = v.estado
                venta_existente.metodo_pago = v.metodo_pago
                venta_existente.updated_at = v.updated_at

    # 4. Sincronizar Gastos del Local
    for g in payload.gastos:
        if not db.query(Gasto).filter(Gasto.id == g.id).first():
            db.add(Gasto(**g.model_dump()))

    # 5. Guardar Logs de Auditoría
    for log in payload.auditoria:
        if not db.query(AuditoriaLog).filter(AuditoriaLog.id == log.id).first():
            db.add(AuditoriaLog(**log.model_dump()))

    db.commit()
    return {"status": "ok", "server_time": datetime.now(timezone.utc).isoformat()}

@app.get("/sync/pull")
def sync_pull(since: Optional[str] = None, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    query_repuestos = db.query(Repuesto)
    query_ventas = db.query(Venta)
    query_gastos = db.query(Gasto)
    query_auditoria = db.query(AuditoriaLog)

    if since:
        fecha_desde = datetime.fromisoformat(since)
        query_repuestos = query_repuestos.filter(Repuesto.updated_at > fecha_desde)
        query_ventas = query_ventas.filter(Venta.updated_at > fecha_desde)
        query_gastos = query_gastos.filter(Gasto.updated_at > fecha_desde)
        query_auditoria = query_auditoria.filter(AuditoriaLog.fecha_hora > fecha_desde)

    repuestos = query_repuestos.all()
    ventas = query_ventas.order_by(Venta.created_at.desc()).limit(300).all()
    gastos = query_gastos.order_by(Gasto.created_at.desc()).all() if current_user.rol == "ADMIN" else []
    usuarios = db.query(Usuario.id, Usuario.nombre, Usuario.rol, Usuario.activo).all()
    logs = query_auditoria.order_by(AuditoriaLog.fecha_hora.desc()).limit(200).all() if current_user.rol == "ADMIN" else []

    ventas_data = []
    for v in ventas:
        detalles = db.query(DetalleVenta).filter(DetalleVenta.venta_id == v.id).all()
        ventas_data.append({
            "id": str(v.id),
            "usuario_id": str(v.usuario_id),
            "cliente_nombre": v.cliente_nombre,
            "metodo_pago": v.metodo_pago,
            "total": float(v.total),
            "estado": v.estado,
            "created_at": v.created_at.isoformat(),
            "updated_at": v.updated_at.isoformat(),
            "detalles": [
                {
                    "id": str(d.id),
                    "venta_id": str(d.venta_id),
                    "repuesto_id": str(d.repuesto_id),
                    "cantidad": d.cantidad,
                    "precio_unitario": float(d.precio_unitario),
                    "subtotal": float(d.subtotal)
                }
                for d in detalles
            ]
        })

    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "usuarios": [{"id": str(u.id), "nombre": u.nombre, "rol": u.rol, "activo": u.activo} for u in usuarios],
        "repuestos": [
            {
                "id": str(r.id),
                "codigo": r.codigo,
                "nombre": r.nombre,
                "marca_compatible": r.marca_compatible,
                "categoria": r.categoria or "General",
                "foto": r.foto,
                "precio_compra": float(r.precio_compra) if current_user.rol == "ADMIN" else 0,
                "precio_venta": float(r.precio_venta),
                "stock_actual": max(0, r.stock_actual),
                "stock_minimo": r.stock_minimo,
                "created_at": r.created_at.isoformat() if r.created_at else r.updated_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
                "updated_by": str(r.updated_by) if r.updated_by else None,
                "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
            }
            for r in repuestos
        ],
        "ventas": ventas_data,
        "gastos": [
            {
                "id": str(g.id),
                "usuario_id": str(g.usuario_id),
                "categoria": g.categoria,
                "descripcion": g.descripcion,
                "monto": float(g.monto),
                "metodo_pago": g.metodo_pago,
                "created_at": g.created_at.isoformat(),
                "updated_at": g.updated_at.isoformat()
            }
            for g in gastos
        ],
        "auditoria": [
            {
                "id": str(l.id),
                "usuario_id": str(l.usuario_id),
                "tabla_afectada": l.tabla_afectada,
                "registro_id": str(l.registro_id),
                "accion": l.accion,
                "detalle_anterior": l.detalle_anterior,
                "detalle_nuevo": l.detalle_nuevo,
                "fecha_hora": l.fecha_hora.isoformat()
            }
            for l in logs
        ]
    }