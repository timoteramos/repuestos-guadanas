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

    # Columnas para preservar negocio propio e invitaciones con aprobación
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS propio_negocio_id UUID"))
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rol_propio VARCHAR(20) DEFAULT 'ADMIN'"))
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS invitacion_negocio_id UUID"))
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS invitacion_rol VARCHAR(20)"))
    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS invitacion_remitente VARCHAR(150)"))

    conn.execute(text("UPDATE usuarios SET propio_negocio_id = negocio_id WHERE propio_negocio_id IS NULL AND negocio_id IS NOT NULL"))

    primer_admin = conn.execute(text("SELECT id, negocio_id FROM usuarios ORDER BY updated_at ASC LIMIT 1")).fetchone()
    if primer_admin:
        neg_id = primer_admin.negocio_id or primer_admin.id
        for tabla in ["usuarios", "repuestos", "ventas", "gastos", "auditoria_logs"]:
            conn.execute(text(f"UPDATE {tabla} SET negocio_id = :neg WHERE negocio_id IS NULL"), {"neg": neg_id})
        conn.execute(text("UPDATE usuarios SET propio_negocio_id = :neg WHERE propio_negocio_id IS NULL"), {"neg": neg_id})

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
    nombre: Optional[str] = ""
    email: str
    password: Optional[str] = ""
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
        user.negocio_id = uuid.uuid4()
        db.commit()
    db.execute(
        text("UPDATE usuarios SET propio_negocio_id = :neg WHERE id = :uid AND propio_negocio_id IS NULL"),
        {"neg": user.negocio_id, "uid": user.id}
    )
    db.commit()
    return user.negocio_id

def obtener_estado_equipo_usuario(user_id: uuid.UUID, db: Session) -> dict:
    row = db.execute(
        text("""
            SELECT negocio_id, propio_negocio_id, rol_propio,
                   invitacion_negocio_id, invitacion_rol, invitacion_remitente
            FROM usuarios WHERE id = :uid
        """),
        {"uid": user_id}
    ).fetchone()
    if not row:
        return {}
    propio = row.propio_negocio_id or row.negocio_id
    en_equipo_invitado = bool(propio and row.negocio_id and str(propio) != str(row.negocio_id))
    invitacion_pendiente = None
    if row.invitacion_negocio_id:
        invitacion_pendiente = {
            "negocio_id": str(row.invitacion_negocio_id),
            "rol": row.invitacion_rol or "VENDEDOR",
            "remitente": row.invitacion_remitente or "Un Administrador"
        }
    return {
        "propio_negocio_id": str(propio) if propio else None,
        "en_equipo_invitado": en_equipo_invitado,
        "invitacion_pendiente": invitacion_pendiente
    }

def normalizar_email(email: str) -> str:
    return email.strip().lower()

def validar_password_segura(password: str):
    if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]", password) or not re.search(r"[0-9]", password):
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener números y mínimo una letra."
        )

def construir_respuesta_sesion(user: Usuario, db: Session) -> dict:
    negocio_id = obtener_negocio_id(user, db)
    estado_eq = obtener_estado_equipo_usuario(user.id, db)
    token = create_access_token({"sub": str(user.id), "rol": user.rol})
    return {
        "access_token": token,
        "usuario": {
            "id": str(user.id),
            "negocio_id": str(negocio_id),
            "nombre": user.nombre,
            "email": user.email,
            "rol": user.rol,
            "en_equipo_invitado": estado_eq.get("en_equipo_invitado", False),
            "invitacion_pendiente": estado_eq.get("invitacion_pendiente")
        }
    }

# --- HEALTHCHECK PARA DESPERTAR SERVIDOR ---
@app.get("/health")
def health():
    return {"status": "ok"}

# --- ENDPOINTS DE AUTENTICACIÓN Y EQUIPO ---

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

    db.execute(
        text("UPDATE usuarios SET propio_negocio_id = :neg, rol_propio = 'ADMIN' WHERE id = :uid"),
        {"neg": negocio_id, "uid": nuevo_id}
    )
    db.commit()
    return construir_respuesta_sesion(nuevo, db)

@app.post("/auth/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    email_limpio = normalizar_email(data.email)
    user = db.query(Usuario).filter(func.lower(func.trim(Usuario.email)) == email_limpio).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")
    if not user.activo:
        raise HTTPException(status_code=403, detail="Usuario desactivado")

    return construir_respuesta_sesion(user, db)

@app.post("/usuarios", dependencies=[Depends(require_admin)])
def invitar_o_crear_usuario(data: CrearUsuarioRequest, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    email_limpio = normalizar_email(data.email)
    if data.rol not in ["ADMIN", "VENDEDOR"]:
        raise HTTPException(status_code=400, detail="El rol seleccionado debe ser Administrador o Vendedor.")
    if "@" not in email_limpio or "." not in email_limpio.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Ingresa un correo electrónico válido (ej. empleado2@correo.com).")

    negocio_id = obtener_negocio_id(current_user, db)
    existente = db.query(Usuario).filter(func.lower(func.trim(Usuario.email)) == email_limpio).first()

    # Caso 1: La cuenta ya existe -> enviar invitación que requiere permiso del usuario
    if existente:
        if str(existente.negocio_id) == str(negocio_id):
            raise HTTPException(status_code=400, detail=f"'{existente.nombre}' ya forma parte de tu equipo.")

        db.execute(
            text("""
                UPDATE usuarios
                SET invitacion_negocio_id = :neg,
                    invitacion_rol = :rol,
                    invitacion_remitente = :rem
                WHERE id = :uid
            """),
            {
                "neg": negocio_id,
                "rol": data.rol,
                "rem": f"{current_user.nombre} ({current_user.email})",
                "uid": existente.id
            }
        )
        db.commit()
        return {
            "mensaje": f"Invitación enviada a {existente.nombre} ({email_limpio}). Cuando inicie sesión o sincronice podrá aceptar o rechazar unirse a tu equipo."
        }

    # Caso 2: Cuenta nueva creada desde el equipo -> conserva su propio espacio privado y recibe invitación pendiente
    if not data.nombre or not data.nombre.strip():
        raise HTTPException(status_code=400, detail="Para crear una cuenta nueva debes escribir su nombre.")
    if not data.password:
        raise HTTPException(status_code=400, detail="Para crear una cuenta nueva debes asignar una contraseña.")
    validar_password_segura(data.password)

    nuevo_id = uuid.uuid4()
    espacio_propio = uuid.uuid4()
    nuevo = Usuario(
        id=nuevo_id,
        negocio_id=espacio_propio,
        nombre=data.nombre.strip(),
        email=email_limpio,
        password_hash=hash_password(data.password),
        rol="ADMIN",
        activo=True,
        updated_at=datetime.now(timezone.utc)
    )
    db.add(nuevo)
    db.commit()

    db.execute(
        text("""
            UPDATE usuarios
            SET propio_negocio_id = :propio,
                rol_propio = 'ADMIN',
                invitacion_negocio_id = :neg,
                invitacion_rol = :rol,
                invitacion_remitente = :rem
            WHERE id = :uid
        """),
        {
            "propio": espacio_propio,
            "neg": negocio_id,
            "rol": data.rol,
            "rem": f"{current_user.nombre} ({current_user.email})",
            "uid": nuevo_id
        }
    )
    db.commit()
    return {
        "mensaje": f"Cuenta creada para {nuevo.nombre} ({email_limpio}) con invitación pendiente. Al entrar le pedirá permiso para unirse a tu equipo.",
        "id": str(nuevo.id)
    }

@app.post("/equipo/responder-invitacion")
def responder_invitacion(aceptar: bool, current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT invitacion_negocio_id, invitacion_rol, propio_negocio_id, negocio_id FROM usuarios WHERE id = :uid"),
        {"uid": current_user.id}
    ).fetchone()

    if not row or not row.invitacion_negocio_id:
        raise HTTPException(status_code=400, detail="No tienes invitaciones pendientes en este momento.")

    if aceptar:
        propio = row.propio_negocio_id or row.negocio_id
        nuevo_rol = row.invitacion_rol or "VENDEDOR"
        db.execute(
            text("""
                UPDATE usuarios
                SET propio_negocio_id = :propio,
                    negocio_id = :inv_neg,
                    rol = :inv_rol,
                    invitacion_negocio_id = NULL,
                    invitacion_rol = NULL,
                    invitacion_remitente = NULL,
                    updated_at = :ahora
                WHERE id = :uid
            """),
            {
                "propio": propio,
                "inv_neg": row.invitacion_negocio_id,
                "inv_rol": nuevo_rol,
                "ahora": datetime.now(timezone.utc),
                "uid": current_user.id
            }
        )
        db.commit()
        db.refresh(current_user)
        sesion = construir_respuesta_sesion(current_user, db)
        sesion["mensaje"] = "Te has unido al equipo. Tus datos personales anteriores siguen guardados de forma segura para cuando decidas salir."
        return sesion
    else:
        db.execute(
            text("""
                UPDATE usuarios
                SET invitacion_negocio_id = NULL,
                    invitacion_rol = NULL,
                    invitacion_remitente = NULL
                WHERE id = :uid
            """),
            {"uid": current_user.id}
        )
        db.commit()
        db.refresh(current_user)
        sesion = construir_respuesta_sesion(current_user, db)
        sesion["mensaje"] = "Invitación rechazada. Sigues en tu cuenta personal."
        return sesion

@app.post("/equipo/salir")
def salir_del_equipo(current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT propio_negocio_id, negocio_id, rol_propio FROM usuarios WHERE id = :uid"),
        {"uid": current_user.id}
    ).fetchone()

    propio = row.propio_negocio_id if row else None
    if not propio or str(propio) == str(row.negocio_id):
        raise HTTPException(status_code=400, detail="Actualmente no estás dentro de un equipo invitado.")

    rol_restaurado = (row.rol_propio if row and row.rol_propio in ["ADMIN", "VENDEDOR"] else "ADMIN")
    db.execute(
        text("""
            UPDATE usuarios
            SET negocio_id = :propio,
                rol = :rol_propio,
                updated_at = :ahora
            WHERE id = :uid
        """),
        {
            "propio": propio,
            "rol_propio": rol_restaurado,
            "ahora": datetime.now(timezone.utc),
            "uid": current_user.id
        }
    )
    db.commit()
    db.refresh(current_user)
    sesion = construir_respuesta_sesion(current_user, db)
    sesion["mensaje"] = "Saliste del equipo invitado. Se restauró tu cuenta personal con toda la información que tenías antes."
    return sesion

def ejecutar_borrado_cuenta(current_user: Usuario, db: Session) -> dict:
    negocio_id = obtener_negocio_id(current_user, db)

    otros_admins = db.query(Usuario).filter(
        Usuario.negocio_id == negocio_id,
        Usuario.rol == "ADMIN",
        Usuario.id != current_user.id
    ).all()

    otros_usuarios = db.query(Usuario).filter(
        Usuario.negocio_id == negocio_id,
        Usuario.id != current_user.id
    ).all()

    # Si hay otro administrador en el negocio, conservar TODA la información del negocio y eliminar solo esta cuenta
    if len(otros_admins) > 0:
        admin_receptor = otros_admins[0]
        db.query(Venta).filter(Venta.usuario_id == current_user.id).update({"usuario_id": admin_receptor.id}, synchronize_session=False)
        db.query(Gasto).filter(Gasto.usuario_id == current_user.id).update({"usuario_id": admin_receptor.id}, synchronize_session=False)
        db.query(AuditoriaLog).filter(AuditoriaLog.usuario_id == current_user.id).update({"usuario_id": admin_receptor.id}, synchronize_session=False)
        db.query(Repuesto).filter(Repuesto.updated_by == current_user.id).update({"updated_by": None}, synchronize_session=False)
        db.delete(current_user)
        db.commit()
        return {"mensaje": "Tu cuenta fue eliminada. La información del negocio se conservó intacta para los demás administradores."}

    # Si es VENDEDOR y no hay más admins (caso raro), reasignar a otro usuario si existe
    if current_user.rol != "ADMIN" and len(otros_usuarios) > 0:
        receptor = otros_usuarios[0]
        db.query(Venta).filter(Venta.usuario_id == current_user.id).update({"usuario_id": receptor.id}, synchronize_session=False)
        db.query(Gasto).filter(Gasto.usuario_id == current_user.id).update({"usuario_id": receptor.id}, synchronize_session=False)
        db.query(AuditoriaLog).filter(AuditoriaLog.usuario_id == current_user.id).update({"usuario_id": receptor.id}, synchronize_session=False)
        db.query(Repuesto).filter(Repuesto.updated_by == current_user.id).update({"updated_by": None}, synchronize_session=False)
        db.delete(current_user)
        db.commit()
        return {"mensaje": "Tu cuenta de usuario fue eliminada sin borrar la información del equipo."}

    # Si es el último administrador único, eliminar toda la información del negocio
    ventas_ids = [v.id for v in db.query(Venta.id).filter(Venta.negocio_id == negocio_id).all()]
    if ventas_ids:
        db.query(DetalleVenta).filter(DetalleVenta.venta_id.in_(ventas_ids)).delete(synchronize_session=False)

    db.query(Venta).filter(Venta.negocio_id == negocio_id).delete(synchronize_session=False)
    db.query(Gasto).filter(Gasto.negocio_id == negocio_id).delete(synchronize_session=False)
    db.query(AuditoriaLog).filter(AuditoriaLog.negocio_id == negocio_id).delete(synchronize_session=False)
    db.query(Repuesto).filter(Repuesto.negocio_id == negocio_id).delete(synchronize_session=False)
    db.query(Usuario).filter(Usuario.negocio_id == negocio_id).delete(synchronize_session=False)
    db.commit()
    return {"mensaje": "Tu cuenta única y toda la información asociada han sido eliminadas por completo."}

@app.post("/auth/eliminar-cuenta")
def eliminar_cuenta_post(current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return ejecutar_borrado_cuenta(current_user, db)

@app.delete("/auth/eliminar-cuenta")
def eliminar_cuenta_delete(current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    return ejecutar_borrado_cuenta(current_user, db)

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
    estado_eq = obtener_estado_equipo_usuario(current_user.id, db)

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
        "usuario_actual": {
            "id": str(current_user.id),
            "negocio_id": str(negocio_id),
            "nombre": current_user.nombre,
            "email": current_user.email,
            "rol": current_user.rol,
            "en_equipo_invitado": estado_eq.get("en_equipo_invitado", False),
            "invitacion_pendiente": estado_eq.get("invitacion_pendiente")
        },
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