"""Configuration management for Candystore."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # RabbitMQ (Bloodbank) Configuration
    rabbit_url: str = "amqp://guest:guest@localhost:5672/"
    exchange_name: str = "events"
    queue_name: str = "candystore.storage"

    # Database Configuration
    database_url: str = "sqlite+aiosqlite:///./candystore.db"

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8683

    # Logging Configuration
    log_level: str = "INFO"
    log_format: str = "json"  # 'json' or 'console'

    # Metrics Configuration
    metrics_enabled: bool = True
    metrics_port: int = 9090

    # Consumer Configuration
    prefetch_count: int = 100  # How many events to prefetch from RabbitMQ
    batch_size: int = 50  # Batch size for database inserts


settings = Settings()
