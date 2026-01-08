from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "polymarket-watch"
    log_level: str = "INFO"
    log_format: str = "json"
    database_host: str = "localhost"
    database_port: int = 5432
    database_user: str = "polymarket"
    database_password: str = "polymarket"
    database_name: str = "polymarket"
    database_url: str | None = None
    sqlalchemy_echo: bool = False
    telegram_bot_token: str = Field(default="CHANGEME", description="Telegram bot token")
    telegram_chat_id: str | None = Field(default=None, description="Default Telegram chat id")
    notifier_dry_run: bool = True
    ingestion_markets_url: str = "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100&order=volume24hr&ascending=false"
    ingestion_trades_url: str = "https://data-api.polymarket.com/trades"
    ingestion_markets_refresh_seconds: int = 600
    ingestion_trades_poll_interval_min_seconds: int = 30
    ingestion_trades_poll_interval_max_seconds: int = 60
    ingestion_backoff_base_seconds: int = 5
    ingestion_backoff_max_seconds: int = 300
    ingestion_client_timeout_seconds: int = 10

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )


settings = Settings()
