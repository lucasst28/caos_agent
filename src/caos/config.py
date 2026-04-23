"""CAOS Agent configuration using pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve absolute path to .env (project root = 2 levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # === Google Cloud ===
    google_cloud_project: str = Field(default="vivaiot-prod")
    google_application_credentials: str | None = Field(default=None)

    # === Pub/Sub Topics ===
    pubsub_sentinel_alerts_topic: str = Field(default="sentinel.alerts")
    pubsub_oracle_predictions_topic: str = Field(default="oracle.predictions")
    pubsub_caos_actions_topic: str = Field(default="caos.actions")

    # === Vertex AI / Google AI Studio ===
    vertex_ai_location: str = Field(default="us-central1")
    vertex_ai_model: str = Field(default="gemini-2.5-flash")
    google_api_key: str | None = Field(default=None, description="API key do Google AI Studio")

    # === External APIs ===
    atlas_api_url: str = Field(default="http://localhost:9000/atlas/")
    sentinel_api_url: str = Field(default="http://localhost:9000/sentinel/")
    oracle_api_url: str = Field(default="http://localhost:9000/oracle/")

    # === LangSmith (Tracing) ===
    langchain_tracing_v2: bool = Field(default=True)
    langchain_api_key: str | None = Field(default=None)
    langchain_project: str = Field(default="caos-agent")

    # === Vertex LLM caching ===
    vertex_system_cache_ttl: str = Field(
        default="300s",
        description="Time-to-live for Oracle system prompt cached content in Vertex AI",
    )

    # === Feature Flags ===
    oracle_bypass_enabled: bool = Field(default=True)
    oracle_bypass_threshold: float = Field(default=0.8)
    pubsub_enabled: bool = Field(default=False, description="Enable Pub/Sub consumers (requires GCP)")

    # === Verdict Weights ===
    weight_atlas: float = Field(default=0.6, ge=0.0, le=1.0)
    weight_oracle: float = Field(default=0.6, ge=0.0, le=1.0)
    weight_severity: float = Field(default=0.3, ge=0.0, le=1.0)

    # === Risk Dimension Weights ===
    weight_risk_physical: float = Field(default=0.35, ge=0.0, le=1.0)
    weight_risk_financial: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_risk_contractual: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_risk_communication: float = Field(default=0.15, ge=0.0, le=1.0)

    # === Rate Limits ===
    redis_url: str | None = Field(default=None, description="Redis connection URL (e.g. redis://localhost:6379/0)")
    weather_api_url: str = Field(default="http://localhost:9000/weather", validation_alias="WEATHER_API_URL")
    llm_daily_token_budget: int = Field(default=2_000_000)
    llm_daily_cost_budget_usd: float = Field(default=50.0)

    # === WORM Storage ===
    worm_storage_dir: str = Field(default="data/worm", description="Directory for WORM verdict logs")

    # === RLHF Learning Loop ===
    rlhf_enabled: bool = Field(default=True, description="Enable Bayesian weight learning from feedback")
    rlhf_data_dir: str = Field(default="data/rlhf", description="Directory for RLHF state persistence")
    rlhf_min_samples: int = Field(default=20, ge=5, description="Min feedback samples before first weight update")
    rlhf_kl_bound: float = Field(default=0.1, ge=0.01, le=1.0, description="Max KL divergence from prior")
    rlhf_batch_size: int = Field(default=10, ge=1, description="Feedbacks before triggering weight update")
    rlhf_exploration_mode: bool = Field(default=False, description="Use Thompson Sampling exploration vs point estimates")

    # === Decision Thresholds ===
    threshold_blocked: float = Field(default=0.0)
    threshold_alert: float = Field(default=0.3)
    threshold_suggest: float = Field(default=0.7)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
