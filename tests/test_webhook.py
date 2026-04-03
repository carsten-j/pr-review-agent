from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "test-secret-for-webhook"

SAMPLE_PR_PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "number": 42,
        "title": "Add feature X",
        "body": "This PR adds feature X",
        "state": "open",
        "user": {"login": "testuser", "id": 12345},
        "html_url": "https://github.com/carsten-j/pr-review-test-repo/pull/42",
        "diff_url": "https://github.com/carsten-j/pr-review-test-repo/pull/42.diff",
        "head": {"ref": "feature-x", "sha": "abc123"},
        "base": {"ref": "main", "sha": "def456"},
        "created_at": "2026-04-03T10:00:00Z",
        "updated_at": "2026-04-03T10:00:00Z",
    },
    "repository": {
        "full_name": "carsten-j/pr-review-test-repo",
        "clone_url": "https://github.com/carsten-j/pr-review-test-repo.git",
        "private": False,
    },
    "sender": {"login": "testuser", "id": 12345},
}


def _sign(payload: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)
    # Clear the lru_cache so Settings picks up the test env var
    from pr_review_agent.main import get_settings

    get_settings.cache_clear()


@pytest.fixture
def client():
    from pr_review_agent.main import app

    return TestClient(app)


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_pr_opened_returns_received(client: TestClient):
    body = json.dumps(SAMPLE_PR_PAYLOAD).encode()
    signature = _sign(body, TEST_SECRET)
    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "received"
    assert data["pr_number"] == "42"
    assert data["title"] == "Add feature X"
    assert data["author"] == "testuser"


def test_invalid_signature_returns_401(client: TestClient):
    body = json.dumps(SAMPLE_PR_PAYLOAD).encode()
    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_missing_signature_returns_401(client: TestClient):
    body = json.dumps(SAMPLE_PR_PAYLOAD).encode()
    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_non_pr_event_is_ignored(client: TestClient):
    body = b'{"action": "completed"}'
    signature = _sign(body, TEST_SECRET)
    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert "push" in response.json()["reason"]


def test_pr_closed_action_is_ignored(client: TestClient):
    payload = {**SAMPLE_PR_PAYLOAD, "action": "closed"}
    body = json.dumps(payload).encode()
    signature = _sign(body, TEST_SECRET)
    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert "closed" in response.json()["reason"]
