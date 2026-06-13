from __future__ import annotations

import math
import os
from typing import Any

try:
    from pydantic_settings import BaseSettings
except ModuleNotFoundError as exc:
    if exc.name != "pydantic_settings":
        raise

    class BaseSettings:  # type: ignore[no-redef]
        """Minimal environment-backed fallback for lightweight local tooling."""

        def __init__(self, **overrides: Any) -> None:
            annotations: dict[str, Any] = {}
            for cls in reversed(type(self).mro()):
                annotations.update(getattr(cls, "__annotations__", {}))

            for key in annotations:
                if key.startswith("_"):
                    continue
                default = getattr(type(self), key, None)
                value = overrides.get(
                    key,
                    os.environ.get(key.upper(), os.environ.get(key, default)),
                )
                setattr(self, key, self._coerce_settings_value(value, default))

        @staticmethod
        def _coerce_settings_value(value: Any, default: Any) -> Any:
            if isinstance(default, bool) and not isinstance(value, bool):
                if isinstance(value, str):
                    return value.strip().lower() in {"1", "true", "yes", "on"}
                return bool(value)
            if isinstance(default, int) and not isinstance(default, bool):
                if isinstance(value, bool):
                    return default
                if isinstance(value, int):
                    return value
                if isinstance(value, float) and not math.isfinite(value):
                    return default
                try:
                    return int(value)
                except (TypeError, ValueError, OverflowError):
                    return default
            if isinstance(default, str) and not isinstance(value, str):
                return str(value)
            return value

from codey.saas.redis_url import normalize_redis_url

_DEFAULT_REDIS_URL = "redis://localhost:6379"


def _normalize_settings_redis_url(value: object) -> str:
    return normalize_redis_url(value) or _DEFAULT_REDIS_URL


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/codey"
    redis_url: str = "redis://localhost:6379"
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    resend_api_key: str = ""
    sendgrid_api_key: str = ""
    email_from: str = "noreply@codey.ai"
    email_from_name: str = "Codey"
    frontend_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"
    anthropic_api_key: str = ""
    s3_bucket: str = "codey-uploads"
    s3_region: str = "us-east-1"

    # AI provider keys
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    mistral_api_key: str = ""
    deepseek_api_key: str = ""
    together_api_key: str = ""
    fireworks_api_key: str = ""
    cloudflare_api_key: str = ""
    cloudflare_account_id: str = ""
    huggingface_api_key: str = ""
    cohere_api_key: str = ""
    cerebras_api_key: str = ""

    # Search keys
    tavily_api_key: str = ""
    brave_search_api_key: str = ""
    exa_api_key: str = ""
    bing_search_api_key: str = ""
    perplexity_api_key: str = ""

    # Code security
    snyk_api_key: str = ""
    nvd_api_key: str = ""
    libraries_io_api_key: str = ""
    semgrep_app_token: str = ""

    # Sandbox
    e2b_api_key: str = ""

    # Monitoring
    sentry_dsn: str = ""

    # Communication
    discord_webhook_url: str = ""
    slack_webhook_url: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Monitoring
    betterstack_api_key: str = ""
    uptimerobot_api_key: str = ""

    # Dev tooling
    linear_api_key: str = ""
    vercel_token: str = ""
    railway_token: str = ""

    # Additional code security
    sonarcloud_token: str = ""
    aikido_api_key: str = ""
    deepsource_token: str = ""

    # GitHub integration
    github_token: str = ""
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_webhook_secret: str = ""

    class Config:
        env_file = ("/etc/secrets/.env", ".env")
        extra = "ignore"

settings = Settings()
settings.redis_url = _normalize_settings_redis_url(settings.redis_url)
