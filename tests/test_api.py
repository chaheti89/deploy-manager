"""
API endpoint tests — uses TestClient with DB startup mocked out.
Tests paths that do not require real external services (Postgres, Claude, Voyage AI).
"""
import hashlib
import hmac
import json

import pytest


SECRET = "test-webhook-secret"   # matches GITHUB_WEBHOOK_SECRET in conftest


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, digestmod=hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ── health check ──────────────────────────────────────────────────────────────

def test_health_returns_200(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"message": "DeployGuard is running"}


# ── dashboard ─────────────────────────────────────────────────────────────────

def test_dashboard_returns_html(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "DeployGuard" in r.text
    assert "text/html" in r.headers["content-type"]


# ── webhook signature enforcement ────────────────────────────────────────────

def test_webhook_rejects_missing_signature(client):
    r = client.post("/webhook", json={"ref": "refs/heads/master"})
    assert r.status_code == 401


def test_webhook_rejects_invalid_signature(client):
    r = client.post(
        "/webhook",
        content=b'{"ref":"refs/heads/master"}',
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=badhash",
        },
    )
    assert r.status_code == 401


def test_webhook_rejects_wrong_prefix(client):
    body = b'{"ref":"refs/heads/master"}'
    r = client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "md5=abc123",
        },
    )
    assert r.status_code == 401


def test_webhook_rejects_tampered_body(client):
    """Sig computed on original body should fail against a modified body."""
    original = b'{"ref":"refs/heads/master"}'
    sig = _sign(original)
    tampered = original + b'"extra":1}'
    r = client.post(
        "/webhook",
        content=tampered,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        },
    )
    assert r.status_code == 401
