from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from sqlalchemy.orm import Session
from typing import Optional
from backend.database import get_db
from backend.config import settings
from backend.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/token")


def _decode_token(token: str, db: Session) -> User:
    """Shared token decode + user lookup logic."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """Standard Bearer token auth for all API endpoints."""
    return _decode_token(token, db)


def get_user_from_query_token(
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
) -> User:
    """
    Auth via ?token= query param.
    Used for browser-accessible endpoints like /report that open in a new
    tab and cannot send Authorization headers.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode_token(token, db)




def get_optional_user(
    request: "Request",
    db: Session = Depends(get_db)
) -> "Optional[User]":
    """
    Optional auth — returns User if valid JWT present, None otherwise.
    Used by chat/voice endpoints so they work in both public and admin mode.
    """
    from fastapi import Request
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    if not token or token == "null":
        return None
    try:
        return _decode_token(token, db)
    except Exception:
        return None

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def require_analyst_or_above(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in ("admin", "analyst"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Analyst or Admin access required")
    return current_user
