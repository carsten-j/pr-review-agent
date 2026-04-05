"""Send a simulated GitHub PR webhook payload to the local server."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("WEBHOOK_URL", "http://localhost:8000")
SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

if not SECRET:
    print(
        "Error: GITHUB_WEBHOOK_SECRET not set. Create a .env file or set the env var."
    )
    sys.exit(1)

payload = {
    "action": "opened",
    "pull_request": {
        "number": 3,
        "title": "Test PR from simulation script",
        "body": "This is a simulated PR to verify the webhook receiver works.",
        "state": "open",
        "user": {"login": "simulator", "id": 99999},
        "html_url": f"{BASE_URL}/fake-pr/3",
        "diff_url": f"{BASE_URL}/fake-pr/3.diff",
        "head": {"ref": "feature-test", "sha": "aaa111"},
        "base": {"ref": "main", "sha": "bbb222"},
        "created_at": "2026-04-03T12:00:00Z",
        "updated_at": "2026-04-03T12:00:00Z",
    },
    "repository": {
        "full_name": "carsten-j/pr-review-test-repo",
        "clone_url": "https://github.com/carsten-j/pr-review-test-repo.git",
        "private": False,
    },
    "sender": {"login": "simulator", "id": 99999},
}

body = json.dumps(payload).encode()
signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

response = httpx.post(
    f"{BASE_URL}/webhook/github",
    content=body,
    headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
    },
)

print(f"Status: {response.status_code}")
print(f"Response: {json.dumps(response.json(), indent=2)}")
