import os

# Must be set before any app module is imported so pydantic Settings() resolves correctly.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("VOYAGE_API_KEY", "va-test")

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """
    FastAPI test client with DB startup mocked out.
    Patches app.main.init_db so the startup event / lifespan does not
    try to connect to a real Postgres instance during tests.
    """
    with patch("app.main.init_db", new_callable=AsyncMock):
        from app.main import app
        with TestClient(app) as c:
            yield c
