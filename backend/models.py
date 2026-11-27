from sqlalchemy import Column, String, Boolean, Integer, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(UUID(as_uuid=True), primary_key=True)
    nombre = Column(String(100), nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    rol = Column(String(20), nullable=False)
    activo = Column(Boolean, default=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

class Repuesto(Base):
    __tablename__ = "repuestos"
    id = Column(UUID(as_uuid=True), primary_key=True)
    codigo = Column(String(50), unique=True, nullable=False)
    nombre = Column(String(150), nullable=False)
    marca_compatible = Column(String(100))
    categoria = Column(String(50), default="General")
    foto = Column(Text, nullable=True)
    precio_compra = Column(Numeric(12, 2), nullable=False, default=0)
    precio_venta = Column(Numeric(12, 2), nullable=False)
    stock_actual = Column(Integer, nullable=False, default=0)
    stock_minimo = Column(Integer, nullable=False, default=3)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

class Venta(Base):
    __tablename__ = "ventas"
    id = Column(UUID(as_uuid=True), primary_key=True)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False)
    cliente_nombre = Column(String(100), default="Mostrador")
    metodo_pago = Column(String(30), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    estado = Column(String(20), default="COMPLETADA")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

class DetalleVenta(Base):
    __tablename__ = "detalle_ventas"
    id = Column(UUID(as_uuid=True), primary_key=True)
    venta_id = Column(UUID(as_uuid=True), ForeignKey("ventas.id", ondelete="CASCADE"), nullable=False)
    repuesto_id = Column(UUID(as_uuid=True), ForeignKey("repuestos.id"), nullable=False)
    cantidad = Column(Integer, nullable=False)
    precio_unitario = Column(Numeric(12, 2), nullable=False)
    subtotal = Column(Numeric(12, 2), nullable=False)

class Gasto(Base):
    __tablename__ = "gastos"
    id = Column(UUID(as_uuid=True), primary_key=True)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False)
    categoria = Column(String(50), nullable=False)
    descripcion = Column(String(255), nullable=False)
    monto = Column(Numeric(12, 2), nullable=False)
    metodo_pago = Column(String(30), default="Efectivo")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

class AuditoriaLog(Base):
    __tablename__ = "auditoria_logs"
    id = Column(UUID(as_uuid=True), primary_key=True)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False)
    tabla_afectada = Column(String(50), nullable=False)
    registro_id = Column(UUID(as_uuid=True), nullable=False)
    accion = Column(String(50), nullable=False)
    detalle_anterior = Column(JSONB, nullable=True)
    detalle_nuevo = Column(JSONB, nullable=True)
    fecha_hora = Column(DateTime(timezone=True), nullable=False)