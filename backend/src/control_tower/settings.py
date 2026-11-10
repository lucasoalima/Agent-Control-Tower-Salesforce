"""Environment-based configuration. Every variable is prefixed with ``ACT_``."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://act:act_dev_password@localhost:5432/act"

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Dashboard sessions
    session_ttl_minutes: int = Field(default=720, ge=5, le=10_080)
    # Set to true whenever the dashboard is served over HTTPS.
    session_cookie_secure: bool = False
    # Comma-separated origins allowed to send state-changing dashboard requests.
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    max_request_bytes: int = Field(default=1_048_576, ge=1_024)

    approval_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    maintenance_interval_seconds: int = Field(default=10, ge=1, le=3_600)
    execution_stale_after_seconds: int = Field(default=300, ge=1, le=86_400)
    maintenance_enabled: bool = True

    # Used by `act seed` to create the first operator. If the password is not
    # set, a random one is generated and printed once.
    operator_username: str = "operator"
    operator_password: SecretStr | None = None

    @field_validator("operator_password")
    @classmethod
    def empty_operator_password_is_unset(cls, value: SecretStr | None) -> SecretStr | None:
        return value if value is not None and value.get_secret_value() else None

    @property
    def allowed_origin_set(self) -> frozenset[str]:
        return frozenset(
            o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
