import uuid
from datetime import datetime, timezone
from typing import List, Optional, Any
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Usuario, Repuesto, Venta, DetalleVenta, AuditoriaLog
from auth import hash_password, verify_password, create_access_token, get_current_user, require_admin

app = FastAPI(title="API Repuestos de Guadañas Offline-First")

# Permitir peticiones desde el frontend en celulares
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
    rol: str  # 'ADMIN' o 'VENDEDOR'

class RepuestoSync(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    marca_compatible: Optional[str] = ""
    categoria: Optional[str] = ""
    precio_compra: float
    precio_venta: float
    stock_actual: int
    stock_minimo: int = 3
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
    detalles: List[DetalleVentaSync]

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
    ventas: List[VentaSync] = []
    auditoria: List[AuditoriaSync] = []

# --- ENDPOINTS DE AUTENTICACIÓN ---

@app.post("/setup-admin")
def crear_primer_admin(data: CrearUsuarioRequest, db: Session = Depends(get_db)):
    """Solo funciona si aún no existe ningún usuario en la base de datos."""
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
    """Recibe cambios creados o editados en los celulares sin internet."""
    # 1. Sincronizar Repuestos (Solo ADMIN puede crear/editar catálogo)
    if current_user.rol == "ADMIN":
        for item in payload.repuestos:
            existente = db.query(Repuesto).filter(Repuesto.id == item.id).first()
            if not existente:
                db.add(Repuesto(**item.model_dump()))
            else:
                # Last-Write-Wins: Solo actualiza si el cambio del celular es más reciente
                if item.updated_at > existente.updated_at:
                    for key, value in item.model_dump().items():
                        setattr(existente, key, value)

    # 2. Sincronizar Ventas nuevas y descontar stock en el servidor
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
                # Descontar stock en el servidor de forma segura
                rep = db.query(Repuesto).filter(Repuesto.id == det.repuesto_id).first()
                if rep and v.estado == "COMPLETADA":
                    rep.stock_actual -= det.cantidad
                    rep.updated_at = datetime.now(timezone.utc)

    # 3. Guardar Logs de Auditoría (Inmutables)
    for log in payload.auditoria:
        if not db.query(AuditoriaLog).filter(AuditoriaLog.id == log.id).first():
            db.add(AuditoriaLog(**log.model_dump()))

    db.commit()
    return {"status": "ok", "server_time": datetime.now(timezone.utc).isoformat()}

@app.get("/sync/pull")
def sync_pull(since: Optional[str] = None, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Envía al celular todos los cambios ocurridos desde su última sincronización."""
    query_repuestos = db.query(Repuesto)
    query_ventas = db.query(Venta)
    query_auditoria = db.query(AuditoriaLog)

    if since:
        fecha_desde = datetime.fromisoformat(since)
        query_repuestos = query_repuestos.filter(Repuesto.updated_at > fecha_desde)
        query_ventas = query_ventas.filter(Venta.updated_at > fecha_desde)
        query_auditoria = query_auditoria.filter(AuditoriaLog.fecha_hora > fecha_desde)

    repuestos = query_repuestos.all()
    usuarios = db.query(Usuario.id, Usuario.nombre, Usuario.rol, Usuario.activo).all()
    
    # Solo el ADMIN descarga los logs de auditoría completos
    logs = query_auditoria.order_by(AuditoriaLog.fecha_hora.desc()).limit(200).all() if current_user.rol == "ADMIN" else []

    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "usuarios": [{"id": str(u.id), "nombre": u.nombre, "rol": u.rol, "activo": u.activo} for u in usuarios],
        "repuestos": [
            {
                "id": str(r.id),
                "codigo": r.codigo,
                "nombre": r.nombre,
                "marca_compatible": r.marca_compatible,
                "categoria": r.categoria,
                "precio_compra": float(r.precio_compra) if current_user.rol == "ADMIN" else 0,
                "precio_venta": float(r.precio_venta),
                "stock_actual": r.stock_actual,
                "stock_minimo": r.stock_minimo,
                "updated_at": r.updated_at.isoformat(),
                "updated_by": str(r.updated_by) if r.updated_by else None,
                "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
            }
            for r in repuestos
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