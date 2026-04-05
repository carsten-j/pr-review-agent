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

FastAPI webhook receiver → triage agent → review agent pipeline, using the raw Anthropic Python SDK (`anthropic`).

**Pipeline flow:** GitHub webhook (`POST /webhook/github`) → signature verification → background task → triage (Haiku 4.5) → if `should_review` → code review (Sonnet 4.5) → log results.

**Triage (single-turn):** `triage.py` makes one `messages.create()` call with `tool_choice={"type": "tool", "name": "produce_triage_result"}` to force a structured `TriageResult` response. No loop needed.

**Review (agentic loop):** `review.py` runs `_run_review_loop()` which loops until Claude calls `submit_review`. Tools (`fetch_pr_diff`, `fetch_file_content`, `search_repo_code`) are closures over the call-scoped context (PR, repo, SHA). A security reviewer runs when triage tags include "security", otherwise a general reviewer runs (persona set via `REVIEWER_ROLE` setting). Guards: `max_iterations=20`, explicit checks for `end_turn` and `max_tokens` stop reasons.

**Client injection:** `AsyncAnthropic()` is created once in `_run_triage_background` in `main.py` and passed to both `run_triage()` and `run_review()`. Tests inject a `MagicMock` instead.

**Git platform abstraction:** `git_platform.py` defines the `GitPlatformClient` Protocol and `RepoDeps` dataclass. `github_client.py` provides the `GitHubClient` implementation (owns a shared `httpx.AsyncClient`, configurable `base_url` for GitHub Enterprise).

**Settings:** `pydantic-settings` `BaseSettings` in `main.py`, populated from env vars (`.env` loaded via `python-dotenv`). Key settings: `github_token`, `github_api_base`, `reviewer_role`.

**Observability:** Logfire is configured at module level in `main.py` via `logfire.configure()` + `logfire.instrument_anthropic()`. Reads `LOGFIRE_TOKEN` from env automatically — no-op if unset. Each PR pipeline run is wrapped in a `logfire.span("review PR {repo}#{pr_number}", ...)` for searchable audit traces.

**Webhook security:** `security.py` provides HMAC-SHA256 verification as a FastAPI dependency. The `verify_webhook_signature` dependency imports `get_settings` from `main.py` at call time to avoid circular imports.

## Testing patterns

- `GitHubClient` tests mock `httpx.AsyncClient.get` directly (the client holds a shared instance)
- Triage and review tests use `MagicMock` + `AsyncMock` for the Anthropic client — `mock_client.messages.create = AsyncMock(return_value=...)`
- Use `FakeGitClient` passed as `git_client=` — no monkeypatching needed
- Integration tests are marked `@pytest.mark.integration` and excluded by default (`addopts = "-m 'not integration'"` in pyproject.toml)
- All tests are async (`asyncio_mode = "auto"`)

## Key type patterns

- Tool dicts require `cast("ToolParam", {...})` to satisfy `ty` — the dicts are correct at runtime but need the cast for static typing
- `cast(ToolUseBlock, block)` is used after a `block.type != "tool_use"` guard for type narrowing — avoids `isinstance` which would break `MagicMock`-based tests
- `cast("list[MessageParam]", messages)` at `messages.create()` call sites for the same reason
