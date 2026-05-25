import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_db, get_redis
from app.models import LoginRequest, RegisterRequest, TokenResponse
from app.services import auth as auth_svc
from app.services import db as db_svc
from app.services.rate_limiter import check_auth_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


async def _enforce_auth_rate_limit(identifier: str) -> None:
    """Block excessive attempts keyed by email to prevent brute-force."""
    redis = get_redis()
    allowed, retry_after = await check_auth_rate_limit(redis, identifier)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest):
    """Create a new tenant account and return a JWT access token."""
    await _enforce_auth_rate_limit(body.email)

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
    except asyncpg.UniqueViolationError:
        # Generic message: don't reveal which field (tenant_id or email) collided
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Registration failed. The tenant ID or email is already in use.",
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


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    """Authenticate with email + password and return a JWT access token."""
    await _enforce_auth_rate_limit(body.email)

    pool = await get_db()
    tenant = await db_svc.get_tenant_by_email(pool, body.email)

    # Always run bcrypt regardless of whether the email exists.
    # Short-circuiting when tenant is None creates a timing oracle that
    # reveals whether an email is registered (~100ms vs ~0ms response delta).
    candidate_hash = tenant["password_hash"] if tenant else auth_svc.DUMMY_HASH
    password_ok = auth_svc.verify_password(body.password, candidate_hash)

    if tenant is None or not password_ok:
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
