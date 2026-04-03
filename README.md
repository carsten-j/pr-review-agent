# PR Review Agent

A GitHub PR review agent powered by Pydantic AI and Anthropic. Currently in **Phase 1**: a FastAPI webhook receiver that detects new pull requests on GitHub and logs them.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — fast Python package manager
- A [GitHub](https://github.com) account
- [ngrok](https://ngrok.com) — for exposing your local server to GitHub webhooks

## Quick start

```bash
# Clone the repo
git clone https://github.com/carsten-j/pr-review-agent.git
cd pr-review-agent

# Install dependencies
uv sync

# Create your .env file
cp .env.example .env
# Edit .env and set GITHUB_WEBHOOK_SECRET to a strong random value.
# You'll use this same secret when configuring the webhook on GitHub (see below).
# Generate one with:
#   python -c "import secrets; print(secrets.token_hex(32))"

# Start the server
uv run uvicorn pr_review_agent.main:app --reload
```

The server runs at `http://localhost:8000`. Verify with:

```bash
curl http://localhost:8000/health
```

## ngrok setup

ngrok creates a public HTTPS tunnel to your local server so GitHub can deliver webhook events to it.

### 1. Install ngrok

```bash
brew install ngrok
```

Or download from [ngrok.com/download](https://ngrok.com/download).

### 2. Create a free account

Sign up at [dashboard.ngrok.com/signup](https://dashboard.ngrok.com/signup).

### 3. Authenticate

After signing up, copy your auth token from the [ngrok dashboard](https://dashboard.ngrok.com/get-started/your-authtoken) and run:

```bash
ngrok config add-authtoken <your-token>
```

### 4. Start the tunnel

With your FastAPI server running on port 8000:

```bash
ngrok http 8000
```

You'll see output like:

```bash
Forwarding  https://a1b2c3d4.ngrok-free.app -> http://localhost:8000
```

Copy the `https://*.ngrok-free.app` URL — you'll need it for the GitHub webhook configuration.

> **Note:** On the free tier, the ngrok URL changes every time you restart the tunnel. You'll need to update the GitHub webhook URL each time. Consider upgrading to a paid plan for a stable domain if you use this regularly.

## GitHub webhook configuration

### 1. Go to your repo's webhook settings

Navigate to your test repo (e.g., `carsten-j/pr-review-test-repo`):

**Settings** → **Webhooks** → **Add webhook**

### 2. Configure the webhook

| Setting | Value |
|---------|-------|
| **Payload URL** | `https://<your-ngrok-url>/webhook/github` |
| **Content type** | `application/json` |
| **Secret** | The same value you set in `.env` as `GITHUB_WEBHOOK_SECRET` |

### 3. Select events

- Choose **"Let me select individual events"**
- Check **"Pull requests"** only
- Uncheck everything else

### 4. Save

Click **Add webhook**. GitHub will send a `ping` event — you'll see it logged as an ignored event (since we only process `pull_request` events), which confirms the connection works.

## Testing

### Run automated tests

```bash
uv run pytest tests/ -v
```

### Simulate a webhook locally

With the server running:

```bash
uv run python scripts/simulate_webhook.py
```

This sends a fake PR webhook with a valid HMAC signature to your local server.

### Live test

1. Start the FastAPI server (`uv run uvicorn pr_review_agent.main:app --reload`)
2. Start ngrok (`ngrok http 8000`)
3. Configure the webhook on GitHub (see above)
4. Open a PR on the test repo
5. Watch the server logs — you should see the PR details logged

## Architecture

```
src/pr_review_agent/
├── main.py       # FastAPI app — webhook endpoint + health check
├── models.py     # Pydantic models for GitHub webhook payloads
└── security.py   # HMAC-SHA256 signature verification
```

- **`main.py`** — Receives `POST /webhook/github`, verifies the signature, filters for `pull_request` events with `action: opened`, and logs the PR details.
- **`models.py`** — Typed Pydantic models for the subset of the GitHub webhook payload we care about.
- **`security.py`** — Verifies the `X-Hub-Signature-256` header using the shared secret. Implemented as a FastAPI dependency.

## What's next

Phase 2 will wire up a Pydantic AI review agent (using Anthropic's Claude) that:

- Fetches the PR diff and file context
- Produces a structured review (risk classification, inline comments, architectural observations)
- Posts review comments back to GitHub
