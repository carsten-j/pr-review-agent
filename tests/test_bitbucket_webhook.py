"""Tests for the /webhook/bitbucket endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "test-bitbucket-secret"

SAMPLE_BB_PAYLOAD = {
    "actor": {
        "nickname": "testuser",
        "display_name": "Test User",
    },
    "pullrequest": {
        "id": 7,
        "title": "Add feature X",
        "description": "This PR adds feature X",
        "state": "OPEN",
        "source": {
            "branch": {"name": "feature-x"},
            "commit": {"hash": "abc123"},
        },
        "destination": {
            "branch": {"name": "main"},
            "commit": {"hash": "def456"},
        },
        "links": {
            "html": {"href": "https://bitbucket.org/workspace/repo/pull-requests/7"},
        },
    },
    "repository": {
        "full_name": "workspace/repo",
        "is_private": False,
    },
}


def _sign(payload: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BITBUCKET_WEBHOOK_SECRET", TEST_SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("BITBUCKET_USERNAME", "testuser")
    monkeypatch.setenv("BITBUCKET_APP_PASSWORD", "fake-app-password")
    # Also set GitHub vars so Settings validation doesn't fail
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    monkeypatch.setenv("GITHUB_TOKEN", "unused")
    from pr_review_agent.main import get_settings

    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    from pr_review_agent.main import app

    return TestClient(app)


def test_pr_created_returns_received(client: TestClient):
    body = json.dumps(SAMPLE_BB_PAYLOAD).encode()
    signature = _sign(body, TEST_SECRET)
    with patch(
        "pr_review_agent.main._run_triage_background",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/webhook/bitbucket",
            content=body,
            headers={
                "X-Event-Key": "pullrequest:created",
                "X-Hub-Signature": signature,
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "received"
    assert data["pr_number"] == "7"
    assert data["title"] == "Add feature X"
    assert data["author"] == "testuser"
    assert "bitbucket.org" in data["url"]


def test_non_created_event_is_ignored(client: TestClient):
    body = json.dumps(SAMPLE_BB_PAYLOAD).encode()
    signature = _sign(body, TEST_SECRET)
    response = client.post(
        "/webhook/bitbucket",
        content=body,
        headers={
            "X-Event-Key": "pullrequest:updated",
            "X-Hub-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert "pullrequest:updated" in response.json()["reason"]


def test_invalid_signature_returns_401(client: TestClient):
    body = json.dumps(SAMPLE_BB_PAYLOAD).encode()
    response = client.post(
        "/webhook/bitbucket",
        content=body,
        headers={
            "X-Event-Key": "pullrequest:created",
            "X-Hub-Signature": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_missing_signature_returns_401(client: TestClient):
    body = json.dumps(SAMPLE_BB_PAYLOAD).encode()
    response = client.post(
        "/webhook/bitbucket",
        content=body,
        headers={
            "X-Event-Key": "pullrequest:created",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401
