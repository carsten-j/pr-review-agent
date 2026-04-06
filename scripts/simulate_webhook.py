"""Send a simulated PR webhook payload to the local server.

Usage:
    uv run python scripts/simulate_webhook.py               # GitHub (default)
    uv run python scripts/simulate_webhook.py --platform bitbucket
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("WEBHOOK_URL", "http://localhost:8000")

parser = argparse.ArgumentParser(description="Simulate a PR webhook event.")
parser.add_argument(
    "--platform",
    choices=["github", "bitbucket"],
    default="github",
    help="Target platform (default: github)",
)
args = parser.parse_args()

if args.platform == "bitbucket":
    SECRET = os.getenv("BITBUCKET_WEBHOOK_SECRET", "")
    if not SECRET:
        print(
            "Error: BITBUCKET_WEBHOOK_SECRET not set. Create a .env file or set the env var."
        )
        sys.exit(1)

    payload = {
        "actor": {
            "nickname": "simulator",
            "display_name": "Simulator",
        },
        "pullrequest": {
            "id": 3,
            "title": "Test PR from simulation script",
            "description": "This is a simulated PR to verify the Bitbucket webhook receiver works.",
            "state": "OPEN",
            "source": {
                "branch": {"name": "feature-test"},
                "commit": {"hash": "aaa111"},
            },
            "destination": {
                "branch": {"name": "main"},
                "commit": {"hash": "bbb222"},
            },
            "links": {
                "html": {"href": f"{BASE_URL}/fake-pr/3"},
            },
        },
        "repository": {
            "full_name": "workspace/pr-review-test-repo",
            "is_private": False,
        },
    }

    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    response = httpx.post(
        f"{BASE_URL}/webhook/bitbucket",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Event-Key": "pullrequest:created",
            "X-Hub-Signature": signature,
        },
    )

else:
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
            "html_url": f"{BASE_URL}/fake-pr/4",
            "diff_url": f"{BASE_URL}/fake-pr/4.diff",
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
