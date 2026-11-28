import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Any
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text, func
from sqlalchemy.orm import Session

from database import get_db, engine
from models import Base, Usuario, Repuesto, Venta, DetalleVenta, AuditoriaLog, Gasto
from auth import hash_password, verify_password, create_access_token, get_current_user, require_admin

Base.metadata.create_all(bind=engine)

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE repuestos ADD COLUMN IF NOT EXISTS foto TEXT"))
    conn.execute(text("ALTER TABLE repuestos ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ"))
    for tabla in ["usuarios", "repuestos", "ventas", "gastos", "auditoria_logs"]:
        conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS negocio_id UUID"))
    conn.execute(text("ALTER TABLE repuestos DROP CONSTRAINT IF EXISTS repuestos_codigo_key"))

    primer_admin = conn.execute(text("SELECT id, negocio_id FROM usuarios ORDER BY updated_at ASC LIMIT 1")).fetchone()
    if primer_admin:
        neg_id = primer_admin.negocio_id or primer_admin.id
        for tabla in ["usuarios", "repuestos", "ventas", "gastos", "auditoria_logs"]:
            conn.execute(text(f"UPDATE {tabla} SET negocio_id = :neg WHERE negocio_id IS NULL"), {"neg": neg_id})

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

class RegistroPublicoRequest(BaseModel):
    nombre: str
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

def obtener_negocio_id(user: Usuario, db: Session) -> uuid.UUID:
    if not user.negocio_id:
        user.negocio_id = user.id
        db.commit()
    return user.negocio_id

def normalizar_email(email: str) -> str:
    return email.strip().lower()

def validar_password_segura(password: str):
    if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]", password) or not re.search(r"[0-9]", password):
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener números y mínimo una letra."
        )

# --- ENDPOINTS DE AUTENTICACIÓN ---

@app.post("/auth/register")
def registrar_cuenta_nueva(data: RegistroPublicoRequest, db: Session = Depends(get_db)):
    email_limpio = normalizar_email(data.email)
    if not data.nombre.strip():
        raise HTTPException(status_code=400, detail="Debes escribir tu nombre o el nombre del local.")
    if "@" not in email_limpio or "." not in email_limpio.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Ingresa un correo electrónico válido (ej. usuario1@correo.com).")
    validar_password_segura(data.password)

    existe = db.query(Usuario).filter(func.lower(func.trim(Usuario.email)) == email_limpio).first()
    if existe:
        raise HTTPException(status_code=400, detail=f"El correo '{email_limpio}' ya está registrado. Inicia sesión o usa otro correo.")

    nuevo_id = uuid.uuid4()
    negocio_id = uuid.uuid4()
    nuevo = Usuario(
        id=nuevo_id,
        negocio_id=negocio_id,
        nombre=data.nombre.strip(),
        email=email_limpio,
        password_hash=hash_password(data.password),
        rol="ADMIN",
        activo=True,
        updated_at=datetime.now(timezone.utc)
    )
    db.add(nuevo)
    db.commit()

    token = create_access_token({"sub": str(nuevo.id), "rol": nuevo.rol})
    return {
        "access_token": token,
        "usuario": {
            "id": str(nuevo.id),
            "negocio_id": str(negocio_id),
            "nombre": nuevo.nombre,
            "email": nuevo.email,
            "rol": nuevo.rol
        }
    }

@app.post("/auth/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    email_limpio = normalizar_email(data.email)
    user = db.query(Usuario).filter(func.lower(func.trim(Usuario.email)) == email_limpio).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")
    if not user.activo:
        raise HTTPException(status_code=403, detail="Usuario desactivado")

    negocio_id = obtener_negocio_id(user, db)
    token = create_access_token({"sub": str(user.id), "rol": user.rol})
    return {
        "access_token": token,
        "usuario": {
            "id": str(user.id),
            "negocio_id": str(negocio_id),
            "nombre": user.nombre,
            "email": user.email,
            "rol": user.rol
        }
    }

@app.post("/usuarios", dependencies=[Depends(require_admin)])
def crear_usuario(data: CrearUsuarioRequest, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    email_limpio = normalizar_email(data.email)
    if data.rol not in ["ADMIN", "VENDEDOR"]:
        raise HTTPException(status_code=400, detail="El rol seleccionado debe ser Administrador o Vendedor.")
    if not data.nombre.strip():
        raise HTTPException(status_code=400, detail="Debes escribir el nombre del usuario.")
    if "@" not in email_limpio or "." not in email_limpio.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Ingresa un correo electrónico válido (ej. empleado2@correo.com).")
    validar_password_segura(data.password)

    existe = db.query(Usuario).filter(func.lower(func.trim(Usuario.email)) == email_limpio).first()
    if existe:
        raise HTTPException(status_code=400, detail=f"El correo '{email_limpio}' ya está registrado en el sistema.")

    negocio_id = obtener_negocio_id(current_user, db)
    nuevo = Usuario(
        id=uuid.uuid4(),
        negocio_id=negocio_id,
        nombre=data.nombre.strip(),
        email=email_limpio,
        password_hash=hash_password(data.password),
        rol=data.rol,
        activo=True,
        updated_at=datetime.now(timezone.utc)
    )
    db.add(nuevo)
    db.commit()
    return {"mensaje": f"Usuario {nuevo.nombre} ({nuevo.rol}) agregado a tu equipo", "id": str(nuevo.id)}

@app.delete("/auth/eliminar-cuenta")
def eliminar_cuenta_actual(current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Elimina la cuenta del usuario actual. Si es el último ADMIN o dueño del negocio, borra toda la información del negocio."""
    negocio_id = obtener_negocio_id(current_user, db)

    otros_admins = db.query(Usuario).filter(
        Usuario.negocio_id == negocio_id,
        Usuario.rol == "ADMIN",
        Usuario.id != current_user.id
    ).count()

    # Si es ADMIN y no quedan otros administradores en ese negocio, borrar absolutamente toda la data del negocio
    if current_user.rol == "ADMIN" and otros_admins == 0:
        ventas_ids = [v.id for v in db.query(Venta.id).filter(Venta.negocio_id == negocio_id).all()]
        if ventas_ids:
            db.query(DetalleVenta).filter(DetalleVenta.venta_id.in_(ventas_ids)).delete(synchronize_session=False)

        db.query(Venta).filter(Venta.negocio_id == negocio_id).delete(synchronize_session=False)
        db.query(Gasto).filter(Gasto.negocio_id == negocio_id).delete(synchronize_session=False)
        db.query(AuditoriaLog).filter(AuditoriaLog.negocio_id == negocio_id).delete(synchronize_session=False)
        db.query(Repuesto).filter(Repuesto.negocio_id == negocio_id).delete(synchronize_session=False)
        db.query(Usuario).filter(Usuario.negocio_id == negocio_id).delete(synchronize_session=False)
        db.commit()
        return {"mensaje": "Tu cuenta y toda la información asociada han sido eliminadas por completo."}

    # Si es VENDEDOR o un ADMIN secundario invitado, eliminar sus registros propios y su cuenta
    db.query(AuditoriaLog).filter(AuditoriaLog.usuario_id == current_user.id).delete(synchronize_session=False)
    db.query(Repuesto).filter(Repuesto.updated_by == current_user.id).update({"updated_by": None}, synchronize_session=False)
    db.delete(current_user)
    db.commit()
    return {"mensaje": "Tu cuenta de usuario ha sido eliminada por completo."}

# --- ENDPOINTS DE SINCRONIZACIÓN AISLADA POR NEGOCIO ---

@app.post("/sync/push")
def sync_push(payload: PushPayload, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    negocio_id = obtener_negocio_id(current_user, db)

    if current_user.rol == "ADMIN":
        for del_id in payload.eliminados:
            rep_borrar = db.query(Repuesto).filter(Repuesto.id == del_id, Repuesto.negocio_id == negocio_id).first()
            if rep_borrar:
                db.query(DetalleVenta).filter(DetalleVenta.repuesto_id == del_id).delete()
                db.delete(rep_borrar)

        for item in payload.repuestos:
            existente = db.query(Repuesto).filter(Repuesto.id == item.id, Repuesto.negocio_id == negocio_id).first()
            if not existente:
                existente = db.query(Repuesto).filter(Repuesto.codigo == item.codigo, Repuesto.negocio_id == negocio_id).first()

            datos = item.model_dump()
            datos["stock_actual"] = max(0, int(datos["stock_actual"]))
            datos["negocio_id"] = negocio_id

            if not existente:
                if not datos.get("created_at"):
                    datos["created_at"] = item.updated_at
                db.add(Repuesto(**datos))
            else:
                if item.updated_at >= existente.updated_at:
                    for key, value in datos.items():
                        if key != "id":
                            setattr(existente, key, value)

    for v in payload.ventas:
        venta_existente = db.query(Venta).filter(Venta.id == v.id, Venta.negocio_id == negocio_id).first()
        if not venta_existente:
            nueva_venta = Venta(
                id=v.id,
                negocio_id=negocio_id,
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
                rep = db.query(Repuesto).filter(Repuesto.id == det.repuesto_id, Repuesto.negocio_id == negocio_id).first()
                if rep:
                    rep.stock_actual = max(0, rep.stock_actual - det.cantidad)
                    rep.updated_at = datetime.now(timezone.utc)
        else:
            if v.updated_at > venta_existente.updated_at:
                venta_existente.estado = v.estado
                venta_existente.metodo_pago = v.metodo_pago
                venta_existente.updated_at = v.updated_at

    for g in payload.gastos:
        if not db.query(Gasto).filter(Gasto.id == g.id, Gasto.negocio_id == negocio_id).first():
            datos_gasto = g.model_dump()
            datos_gasto["negocio_id"] = negocio_id
            db.add(Gasto(**datos_gasto))

    for log in payload.auditoria:
        if not db.query(AuditoriaLog).filter(AuditoriaLog.id == log.id, AuditoriaLog.negocio_id == negocio_id).first():
            datos_log = log.model_dump()
            datos_log["negocio_id"] = negocio_id
            db.add(AuditoriaLog(**datos_log))

    db.commit()
    return {"status": "ok", "server_time": datetime.now(timezone.utc).isoformat()}

@app.get("/sync/pull")
def sync_pull(since: Optional[str] = None, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    negocio_id = obtener_negocio_id(current_user, db)

    query_repuestos = db.query(Repuesto).filter(Repuesto.negocio_id == negocio_id)
    query_ventas = db.query(Venta).filter(Venta.negocio_id == negocio_id)
    query_gastos = db.query(Gasto).filter(Gasto.negocio_id == negocio_id)
    query_auditoria = db.query(AuditoriaLog).filter(AuditoriaLog.negocio_id == negocio_id)

    if since:
        fecha_desde = datetime.fromisoformat(since)
        query_repuestos = query_repuestos.filter(Repuesto.updated_at > fecha_desde)
        query_ventas = query_ventas.filter(Venta.updated_at > fecha_desde)
        query_gastos = query_gastos.filter(Gasto.updated_at > fecha_desde)
        query_auditoria = query_auditoria.filter(AuditoriaLog.fecha_hora > fecha_desde)

    repuestos = query_repuestos.all()
    ventas = query_ventas.order_by(Venta.created_at.desc()).limit(500).all()
    gastos = query_gastos.order_by(Gasto.created_at.desc()).all() if current_user.rol == "ADMIN" else []
    usuarios = db.query(Usuario.id, Usuario.nombre, Usuario.email, Usuario.rol, Usuario.activo).filter(Usuario.negocio_id == negocio_id).all()
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
        "usuarios": [{"id": str(u.id), "nombre": u.nombre, "email": u.email, "rol": u.rol, "activo": u.activo} for u in usuarios],
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