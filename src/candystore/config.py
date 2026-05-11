"""Configuration management for Candystore.

Dapr-pull model: Candystore advertises subscriptions via GET /dapr/subscribe;
Dapr POSTs CloudEvents envelopes to the configured route. No broker client
lives in this process.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database — Postgres in prod; SQLite is allowed for local dev only.
    database_url: str = "postgresql+asyncpg://candystore:candystore@localhost:5432/candystore"

    # Application HTTP server. Dapr sidecar talks to this via --app-port.
    app_host: str = "0.0.0.0"
    app_port: int = 8683

    # Dapr pub/sub component name (declared in bloodbank/compose/components/pubsub.yaml).
    pubsub_name: str = "bloodbank-pubsub"

    # Comma-separated list of CloudEvents `type` values to subscribe to.
    # Default covers the full Claude Code agent.* surface; producers in
    # 33GOD/.claude/hooks/bloodbank-publisher.sh emit these.
    subscribe_topics: str = (
        "event.agent.tool.invoked,"
        "event.agent.tool.requested,"
        "event.agent.session.started,"
        "event.agent.session.ended,"
        "event.agent.prompt.submitted,"
        "event.agent.subagent.completed"
    )

    # Dead-letter topic (Dapr can route undeliverable messages here).
    # Empty disables DLQ wiring at the subscription level.
    dead_letter_topic: str = ""

    # Route the application advertises to Dapr.
    subscribe_route: str = "/events/claude"

    # Legacy compatibility for query API — kept aliased.
    api_host: str = "0.0.0.0"
    api_port: int = 8683

    # Logging Configuration
    log_level: str = "INFO"
    log_format: str = "json"  # 'json' or 'console'

    # Metrics Configuration
    metrics_enabled: bool = True
    metrics_port: int = 9090

    @property
    def topics(self) -> list[str]:
        return [t.strip() for t in self.subscribe_topics.split(",") if t.strip()]


settings = Settings()
