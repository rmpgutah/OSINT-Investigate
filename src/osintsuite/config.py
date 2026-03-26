"""Application configuration loaded from environment variables and .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://osint:osint_dev_password@localhost:5432/osint_db"
    database_url_sync: str = "postgresql+psycopg://osint:osint_dev_password@localhost:5432/osint_db"

    # Application
    app_name: str = "OSINT Investigation Suite"
    debug: bool = False
    reports_dir: Path = Path("./reports")
    secret_key: str = "change-me-in-production"

    # HTTP client
    http_timeout: int = 30
    http_max_retries: int = 3
    http_rate_limit_per_second: float = 2.0
    user_agent: str = "Mozilla/5.0 (compatible; OSINT-Suite/0.1)"

    # Optional API keys
    hibp_api_key: str | None = None
    shodan_api_key: str | None = None
    virustotal_api_key: str | None = None
    abuseipdb_api_key: str | None = None

    # Web interface
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    model_config = {"env_file": ".env", "env_prefix": "OSINT_"}


def get_settings() -> Settings:
    return Settings()
