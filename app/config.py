from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    github_webhook_secret: str
    voyage_api_key: str
    github_token: Optional[str] = None        # optional — raises GitHub API rate limit from 60 to 5000 req/hr
    slack_webhook_url: Optional[str] = None   # optional — enables Slack alerts on HIGH/CRITICAL deploys

    # Email notification settings (all optional)
    smtp_host: Optional[str] = None           # e.g. smtp.gmail.com
    smtp_port: int = 587
    smtp_user: Optional[str] = None           # sender address
    smtp_password: Optional[str] = None       # app password / API key
    alert_email_to: Optional[str] = None      # recipient address

    class Config:
        env_file = ".env"

settings = Settings()