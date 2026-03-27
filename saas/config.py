from __future__ import annotations

from pydantic_settings import BaseSettings


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

    class Config:
        env_file = ".env"


settings = Settings()
