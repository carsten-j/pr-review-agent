# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run server (auto-reload)
uv run uvicorn pr_review_agent.main:app --reload

# Run all unit tests
uv run pytest -v

# Run a single test
uv run pytest tests/test_review.py::test_general_review_returns_structured_output -v

# Run integration tests (requires ANTHROPIC_API_KEY)
uv run pytest -m integration

# Lint and format
uv run ruff check src/ tests/ scripts/
uv run ruff format --check src/ tests/ scripts/

# Type check
uv run ty check src/ tests/ scripts/
```

## Architecture

FastAPI webhook receiver → triage agent → review agent pipeline, all using Pydantic AI. Supports both GitHub and Bitbucket.

**Pipeline flow:** GitHub (`POST /webhook/github`) or Bitbucket (`POST /webhook/bitbucket`) webhook → signature verification → background task → triage (Haiku 4.5) → if `should_review` → code review (Sonnet 4.5) → post review comments → log results.

**Two review agents** in `review.py`: `security_review_agent` runs when triage tags include "security", otherwise `general_review_agent` runs. Both share the same tool list (`fetch_pr_diff`, `fetch_file_content`, `search_repo_code`) passed via `tools=[...]` in the `Agent` constructor. The general agent's persona is injected at request time via `@general_review_agent.instructions` reading `ctx.deps.reviewer_role`.

**Dependency injection pattern:** Each agent defines a `*Deps` dataclass (`TriageDeps`, `ReviewDeps`) passed through `RunContext`. Both extend `RepoDeps` (from `git_platform.py`) which carries `git_client`, `workspace`, `repo_slug`, and `pr_id`. Tools access all fields directly from `ctx.deps`.

**Git platform abstraction:** `git_platform.py` defines the `GitPlatformClient` Protocol and `RepoDeps` dataclass. `github_client.py` provides `GitHubClient` (token auth, configurable `base_url` for GitHub Enterprise). `bitbucket_client.py` provides `BitbucketClient` (HTTP Basic auth via username + app password; degrades gracefully when code search is unavailable on Standard/Free plans). `main.py` selects the appropriate client based on the incoming webhook platform.

**Models:** `models.py` contains platform-specific webhook models (`GitHubWebhookPayload`, `BitbucketWebhookPayload`) and platform-agnostic domain objects (`PullRequestInfo`, `RepoInfo`). `main.py` converts webhook payloads to domain objects via `_github_payload_to_domain` / `_bitbucket_payload_to_domain` before passing them to agents.

**Settings:** `pydantic-settings` `BaseSettings` in `main.py`, populated from env vars (`.env` loaded via `python-dotenv`). Key settings: `github_token`, `github_api_base`, `bitbucket_username`, `bitbucket_app_password`, `bitbucket_webhook_secret`, `bitbucket_api_base`, `reviewer_role`.

**Observability:** Logfire is configured at module level in `main.py` via `logfire.configure()` + `logfire.instrument_pydantic_ai()`. Reads `LOGFIRE_TOKEN` from env automatically — no-op if unset. Each PR pipeline run is wrapped in a `logfire.span("review PR {repo}#{pr_number}", ...)` for searchable audit traces. No changes needed in agent files.

**Webhook security:** `security.py` provides HMAC-SHA256 verification as FastAPI dependencies. `verify_webhook_signature` checks `X-Hub-Signature-256` (GitHub). `verify_bitbucket_webhook_signature` checks `X-Hub-Signature` (Bitbucket). Both import `get_settings` from `main.py` at call time to avoid circular imports.

## Testing patterns

- `GitHubClient` and `BitbucketClient` tests mock `httpx.AsyncClient` methods directly (the clients hold shared instances)
- Agent tests use `agent.override(model=TestModel())` context manager — never set `agent.model` directly
- Review and triage tests use a `FakeGitClient` class passed as `git_client=` — no monkeypatching needed
- Bitbucket webhook tests (`test_bitbucket_webhook.py`) use `app.dependency_overrides` to bypass signature verification
- Integration tests are marked `@pytest.mark.integration` and excluded by default (`addopts = "-m 'not integration'"` in pyproject.toml)
- All tests are async (`asyncio_mode = "auto"`)

## Key type suppressions

`# ty: ignore[invalid-return-type]` appears on `result.output` returns from agent runs — this is a known Pydantic AI generic typing limitation.
