"""Settings — single source of truth for env-driven configuration.

Pydantic-settings loads .env (if present) and validates every value. Modules
should import `settings` and reach into it; do NOT call `os.environ.get(...)`
scattered across the codebase.

Tunable defaults match CLAUDE.md and adviserplan.md. Secrets have no defaults
so a missing value fails loudly at startup.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- LLM ----
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-chat"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ---- Observability ----
    langsmith_api_key: str = ""
    langsmith_project: str = "hitl-support-agent"
    langsmith_tracing: bool = True
    # Empty → SDK default (US: https://api.smith.langchain.com).
    # Set to https://eu.api.smith.langchain.com for EU-region accounts.
    langsmith_endpoint: str = ""

    # ---- Gmail ----
    gmail_user: str = ""
    gmail_app_password: str = ""
    gmail_imap_host: str = "imap.gmail.com"
    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587

    # ---- Slack ----
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_app_token: str = ""

    # 3-channel set per adviserplan.md (cut from spec's 6 — config-driven).
    slack_channel_refunds: str = "#support-refunds"
    slack_channel_technical: str = "#support-technical"
    slack_channel_complaints: str = "#support-complaints"

    # ---- Persistence ----
    sqlite_checkpoint_path: str = "./data/checkpoints.sqlite"

    # ---- PII vault ----
    # Empty → in-memory only (C1/C2 hardening: PII never on disk, never to
    # LangSmith). Non-empty → SQLite sidecar at the given path persists
    # envelope_from + token_map so a paused ticket can resume after process
    # death. Required for the "kill-mid-interrupt + resume" demo. Opt-in:
    # threat_model.md A1 documents the trade-off (recipient + redaction
    # token-map now sit on disk; mitigate with disk encryption / SELinux).
    pii_vault_db_path: str = ""

    # ---- Tunables (defaults match CLAUDE.md) ----
    revalidate_threshold_min: int = 15
    max_human_rejections: int = 3
    max_send_retries: int = 3
    sla_deadline_hours: int = 24
    imap_poll_interval_sec: int = 30

    # ---- Server ----
    # Default to loopback only (127.0.0.1) — safer default for dev. Deployment
    # configs that need to accept external traffic must explicitly set HOST=0.0.0.0
    # via the env (Docker, container platforms typically already do this).
    # bandit B104 was triggered by the previous "0.0.0.0" default; threat model
    # documents the choice in docs/threat_model.md.
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def channel_set(self) -> dict[str, str]:
        """Map of channel-key → channel-name. Used by slack_router."""
        return {
            "refunds": self.slack_channel_refunds,
            "technical": self.slack_channel_technical,
            "complaints": self.slack_channel_complaints,
        }

    def require_secrets(self, *names: str) -> None:
        """Fail loudly if a secret is empty. Call at startup of each I/O module."""
        missing = [n for n in names if not getattr(self, n, "")]
        if missing:
            raise RuntimeError(
                f"Missing required secrets in .env: {', '.join(missing)}. "
                f"See .env.example for the full list."
            )


settings = Settings()
