from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str
    anthropic_api_key: str
    github_webhook_secret: str
    voyage_api_key: str
    github_token: Optional[str] = None         # optional — raises GitHub API rate limit 60→5000/hr
    slack_webhook_url: Optional[str] = None    # optional — enables Slack alerts on HIGH/CRITICAL deploys

    # Email notification settings (all optional)
    smtp_host: Optional[str] = None            # e.g. smtp.gmail.com
    smtp_port: int = 587
    smtp_user: Optional[str] = None            # sender address
    smtp_password: Optional[str] = None        # app password / API key
    alert_email_to: Optional[str] = None       # recipient address

    @field_validator("github_webhook_secret")
    @classmethod
    def secret_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "GITHUB_WEBHOOK_SECRET cannot be an empty string — "
                "set a real secret or remove the variable entirely for dev mode"
            )
        return v


settings = Settings()
