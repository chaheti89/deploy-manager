from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    github_webhook_secret: str
    voyage_api_key: str

    class Config:
        env_file = ".env"

settings = Settings()