# PR Review Agent — Phase 1: GitHub Webhook Receiver

## Context

Building the foundation for a PR review agent (Pydantic AI + Anthropic). This first phase is intentionally minimal: a FastAPI service that receives GitHub webhook events for new PRs and logs them. Structured cleanly so Bitbucket support can be added later without rewriting. Includes webhook signature verification from day one.

## Decisions captured

- **Platform**: GitHub primary, Bitbucket later (no abstraction layer yet — just clean structure)
- **Events**: PR opened only (for now)
- **Action on event**: Log it (no review agent wired up yet)
- **Security**: HMAC signature verification (X-Hub-Signature-256)
- **Hosting**: Local + ngrok for development
- **Stack**: Python, FastAPI, uv
- **Test repo**: Public repo `carsten-j/pr-review-test-repo` on GitHub

## Project structure

```
pr-review-agent/
├── pyproject.toml          # uv project config, dependencies
├── .env.example            # Template for required env vars
├── .gitignore
├── README.md               # Setup guide, ngrok instructions, architecture overview
├── src/
│   └── pr_review_agent/
│       ├── __init__.py
│       ├── main.py         # FastAPI app, webhook endpoint
│       ├── models.py       # Pydantic models for GitHub webhook payloads
│       └── security.py     # HMAC signature verification
├── tests/
│   └── test_webhook.py     # Test with simulated GitHub payloads
└── scripts/
    └── simulate_webhook.py # Send a fake webhook locally for manual testing
```

## Implementation steps

### 1. Initialize project

- `uv init` in project directory
- Add dependencies: `fastapi`, `uvicorn`, `python-dotenv`
- Add dev dependencies: `pytest`, `httpx` (for TestClient)
- Create `.gitignore` (Python defaults + `.env`)
- Create `.env.example` with `GITHUB_WEBHOOK_SECRET`

### 2. Create GitHub webhook payload models (`src/pr_review_agent/models.py`)

- `GitHubUser`: login, id, avatar_url
- `GitHubRepo`: full_name, clone_url, private
- `GitHubPullRequest`: number, title, body, state, user, html_url, diff_url, head/base refs, created_at, updated_at
- `GitHubWebhookPayload`: action, pull_request, repository, sender
- Keep models focused on fields we actually use — not the full GitHub schema

### 3. Webhook signature verification (`src/pr_review_agent/security.py`)

- Function `verify_github_signature(payload_body: bytes, signature: str, secret: str) -> bool`
- Uses `hmac.compare_digest` with SHA-256
- FastAPI dependency that reads raw body, verifies, raises 401 if invalid

### 4. FastAPI webhook endpoint (`src/pr_review_agent/main.py`)

- `POST /webhook/github` — receives webhook, verifies signature, parses payload
- Filter on `X-GitHub-Event: pull_request` header and `action == "opened"`
- Log: PR number, title, author, repo, URL
- Return 200 with acknowledgment
- Health check endpoint `GET /health`

### 5. Tests (`tests/test_webhook.py`)

- Test valid PR opened payload → 200, correct log output
- Test invalid signature → 401
- Test non-PR event (e.g., push) → 200 with "ignored" response
- Test PR event with action != "opened" (e.g., "closed") → 200 with "ignored"

### 6. Simulation script (`scripts/simulate_webhook.py`)

- Sends a realistic GitHub PR webhook payload to localhost
- Computes valid HMAC signature using the local secret
- Quick way to manually verify the service works

### 7. Create test repo on GitHub

- `gh repo create carsten-j/pr-review-test-repo --public`
- Add a README so it's not empty

### 8. README.md

Comprehensive documentation covering:

- **What this is**: one-paragraph project overview and current scope
- **Prerequisites**: Python 3.12+, uv, ngrok account, GitHub account
- **Quick start**: clone, install deps, set env vars, run server
- **ngrok setup** (detailed):
  - Install: `brew install ngrok` (or download from ngrok.com)
  - Sign up for free account at <https://dashboard.ngrok.com/signup>
  - Authenticate: `ngrok config add-authtoken <your-token>` (token from dashboard)
  - Start tunnel: `ngrok http 8000`
  - Copy the `https://*.ngrok-free.app` forwarding URL
  - Note: free tier URLs change every restart — you'll need to update the GitHub webhook URL each time
- **GitHub webhook configuration** (step-by-step with settings):
  - Go to repo → Settings → Webhooks → Add webhook
  - Payload URL: `https://<your-ngrok-url>/webhook/github`
  - Content type: `application/json`
  - Secret: same value as `GITHUB_WEBHOOK_SECRET` in `.env`
  - Events: select "Pull requests" only
  - Save
- **Testing**: how to run tests, simulation script, and live test
- **Architecture**: brief description of what each module does
- **What's next**: placeholder noting the Pydantic AI review agent is the next phase

### 9. Git init + first commit

- Initialize git in project directory
- Commit all files

## Verification

1. Start server: `uv run uvicorn src.pr_review_agent.main:app --reload`
2. Run tests: `uv run pytest tests/`
3. Run simulation script: `uv run python scripts/simulate_webhook.py`
4. For live testing: expose via ngrok, configure webhook on `carsten-j/pr-review-test-repo`, open a PR, confirm log output
