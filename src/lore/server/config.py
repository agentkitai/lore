"""Server configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Settings:
    """Application settings loaded from environment."""

    database_url: str = ""
    redis_url: str = ""
    host: str = "0.0.0.0"
    port: int = 8765
    migrations_dir: str = "migrations"

    # Rate limiting
    rate_limit_backend: str = "memory"  # "memory" or "redis"

    # OIDC / JWT validation
    oidc_issuer: Optional[str] = None
    oidc_audience: Optional[str] = None
    oidc_role_claim: str = "role"
    oidc_org_claim: str = "tenant_id"

    # Auth mode: "dual" | "oidc-required" | "api-key-only"
    auth_mode: str = "api-key-only"

    # Observability
    metrics_enabled: bool = True
    log_format: str = "pretty"  # "json" or "pretty"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        # Resolve Docker secrets / AWS Secrets Manager before reading env
        from lore.server.secrets import apply_secrets_to_env
        apply_secrets_to_env()

        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            redis_url=os.environ.get("REDIS_URL", ""),
            rate_limit_backend=os.environ.get("RATE_LIMIT_BACKEND", "memory"),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8765")),
            migrations_dir=os.environ.get("MIGRATIONS_DIR", "migrations"),
            oidc_issuer=os.environ.get("OIDC_ISSUER"),
            oidc_audience=os.environ.get("OIDC_AUDIENCE"),
            oidc_role_claim=os.environ.get("OIDC_ROLE_CLAIM", "role"),
            oidc_org_claim=os.environ.get("OIDC_ORG_CLAIM", "tenant_id"),
            auth_mode=os.environ.get("AUTH_MODE", "api-key-only"),
            metrics_enabled=os.environ.get("METRICS_ENABLED", "true").lower() in ("true", "1", "yes"),
            log_format=os.environ.get("LOG_FORMAT", "pretty"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )


settings = Settings.from_env()
