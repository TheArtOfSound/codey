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
