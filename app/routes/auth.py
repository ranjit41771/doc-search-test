import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_db
from app.models import LoginRequest, RegisterRequest, TokenResponse
from app.services import auth as auth_svc
from app.services import db as db_svc

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest):
    """Create a new tenant account and return a JWT access token."""
    pool = await get_db()
    password_hash = auth_svc.hash_password(body.password)

    try:
        tenant = await db_svc.register_tenant(
            pool,
            tenant_id=body.tenant_id,
            name=body.name,
            email=body.email,
            password_hash=password_hash,
            plan=body.plan,
        )
    except asyncpg.UniqueViolationError as e:
        detail = (
            "Tenant ID is already taken."
            if "tenants_pkey" in str(e)
            else "An account with this email already exists."
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    token = auth_svc.create_access_token(
        tenant_id=tenant["id"],
        email=tenant["email"],
        name=tenant["name"],
        plan=tenant["plan"],
    )
    return TokenResponse(
        access_token=token,
        tenant_id=tenant["id"],
        name=tenant["name"],
        plan=tenant["plan"],
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    """Authenticate with email + password and return a JWT access token."""
    pool = await get_db()
    tenant = await db_svc.get_tenant_by_email(pool, body.email)

    if tenant is None or not auth_svc.verify_password(body.password, tenant["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_svc.create_access_token(
        tenant_id=tenant["id"],
        email=tenant["email"],
        name=tenant["name"],
        plan=tenant["plan"],
    )
    return TokenResponse(
        access_token=token,
        tenant_id=tenant["id"],
        name=tenant["name"],
        plan=tenant["plan"],
    )


@router.get("/me", response_model=TokenResponse)
async def me(token_data: dict = Depends(auth_svc.decode_access_token_dep)):
    """Return the current authenticated tenant's info."""
    return TokenResponse(
        access_token="",   # not re-issued here
        tenant_id=token_data["sub"],
        name=token_data["name"],
        plan=token_data["plan"],
    )
