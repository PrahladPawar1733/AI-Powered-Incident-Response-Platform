import jwt
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from shared.config import settings
from shared.logger import get_logger

log = get_logger("auth")

security = HTTPBearer(auto_error=False)

async def extract_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(security)
) -> str:
    """
    FastAPI dependency to extract tenant_id from the JWT token.
    If no token is provided in development, falls back to the default tenant.
    In production, rejects the request if no valid token is present.
    """
    if not credentials:
        if settings.environment == "development":
            log.warning("auth_no_token_fallback", tenant_id=settings.default_tenant_id)
            request.state.tenant_id = settings.default_tenant_id
            return settings.default_tenant_id
        
        log.warning("auth_missing_token_rejected")
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization Header"
        )
    
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            log.warning("auth_token_missing_tenant")
            raise HTTPException(
                status_code=401,
                detail="Token payload missing tenant_id"
            )
        
        request.state.tenant_id = tenant_id
        return tenant_id
        
    except jwt.ExpiredSignatureError:
        log.warning("auth_token_expired")
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        log.warning("auth_token_invalid", error=str(e))
        raise HTTPException(status_code=401, detail="Invalid token")
