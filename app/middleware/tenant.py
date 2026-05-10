from fastapi import Depends

from app.services.auth import decode_access_token_dep


async def get_tenant_id(token_data: dict = Depends(decode_access_token_dep)) -> str:
    """Extract tenant_id from the validated JWT payload."""
    return token_data["sub"]
