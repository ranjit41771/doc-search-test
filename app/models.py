from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    tenant_id: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-z0-9][a-z0-9\-]+[a-z0-9]$",
                           description="Unique tenant identifier, e.g. 'acme-corp'. Lowercase letters, numbers, hyphens only.")
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8)
    plan: str = Field(default="free", pattern="^(free|standard|enterprise)$")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: str
    name: str
    plan: str


class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SearchResult(BaseModel):
    id: str
    tenant_id: str
    title: str
    content: str
    metadata: dict[str, Any]
    score: float
    highlights: dict[str, list[str]] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    tenant_id: str
    total: int
    took_ms: int
    results: list[SearchResult]
    cached: bool = False


class HealthDependency(BaseModel):
    status: str
    latency_ms: float


class HealthResponse(BaseModel):
    status: str
    dependencies: dict[str, HealthDependency]
