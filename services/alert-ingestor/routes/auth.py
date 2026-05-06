"""
Auth API — Login and Register endpoints for multi-tenant authentication.

Tenants register with an org name and password. On login, they receive a
JWT containing their tenant_id. The frontend stores this token and sends
it with every API request.
"""
from __future__ import annotations
import hashlib
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.config import settings
from shared.logger import get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("auth-api")

# In-memory user store for development. In production, use PostgreSQL.
# Format: { "tenant_id": { "password_hash": "...", "org_name": "..." } }
_users: dict[str, dict] = {
    "default": {
        "password_hash": hashlib.sha256("admin".encode()).hexdigest(),
        "org_name": "Default Organization",
    }
}


class RegisterRequest(BaseModel):
    tenant_id: str
    org_name: str
    password: str


class LoginRequest(BaseModel):
    tenant_id: str
    password: str


@router.post("/register", summary="Register a new tenant")
async def register(req: RegisterRequest):
    """Register a new tenant organization."""
    if req.tenant_id in _users:
        raise HTTPException(status_code=409, detail="Tenant ID already exists")

    if len(req.tenant_id) < 2 or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="tenant_id must be >= 2 chars, password >= 4 chars")

    _users[req.tenant_id] = {
        "password_hash": hashlib.sha256(req.password.encode()).hexdigest(),
        "org_name": req.org_name,
    }

    log.info("tenant_registered", tenant_id=req.tenant_id, org=req.org_name)

    # Generate token immediately after registration
    token = _create_token(req.tenant_id)

    return {
        "message": f"Tenant '{req.tenant_id}' registered successfully",
        "tenant_id": req.tenant_id,
        "org_name": req.org_name,
        "token": token,
    }


@router.post("/login", summary="Login and get JWT token")
async def login(req: LoginRequest):
    """Authenticate and receive a JWT token."""
    user = _users.get(req.tenant_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid tenant ID or password")

    password_hash = hashlib.sha256(req.password.encode()).hexdigest()
    if password_hash != user["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid tenant ID or password")

    token = _create_token(req.tenant_id)

    log.info("tenant_login", tenant_id=req.tenant_id)

    return {
        "token": token,
        "tenant_id": req.tenant_id,
        "org_name": user["org_name"],
    }


@router.get("/me", summary="Get current user info from token")
async def get_me(token: str):
    """Verify a token and return the tenant info."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        tenant_id = payload.get("tenant_id")
        user = _users.get(tenant_id)
        return {
            "tenant_id": tenant_id,
            "org_name": user["org_name"] if user else tenant_id,
            "exp": payload.get("exp"),
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _create_token(tenant_id: str) -> str:
    """Create a JWT token with 24-hour expiry."""
    payload = {
        "tenant_id": tenant_id,
        "sub": tenant_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
