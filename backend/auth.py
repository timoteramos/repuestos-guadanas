import os
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
from models import Usuario

SECRET_KEY = os.getenv("SECRET_KEY", "clave_temporal_desarrollo")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=30)  # Dura 30 días para trabajo offline
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Usuario:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token inválido")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token expirado o inválido")

    user = db.query(Usuario).filter(Usuario.id == user_id, Usuario.activo == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")
    return user

def require_admin(current_user: Usuario = Depends(get_current_user)) -> Usuario:
    if current_user.rol != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el Administrador puede realizar esta acción"
        )
    return current_user