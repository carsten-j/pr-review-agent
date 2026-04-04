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

FastAPI webhook receiver → triage agent → review agent pipeline, all using Pydantic AI.

**Pipeline flow:** GitHub webhook (`POST /webhook/github`) → signature verification → background task → triage (Haiku 4.5) → if `should_review` → code review (Sonnet 4.5) → log results.

**Two review agents** in `review.py`: `security_review_agent` runs when triage tags include "security", otherwise `general_review_agent` runs. Both share the same tool set (`get_pr_diff`, `get_file_content`, `search_code`) registered via `_register_tools()`. The general agent's persona is configured via `REVIEWER_ROLE` env var.

**Dependency injection pattern:** Each agent defines a `*Deps` dataclass (`TriageDeps`, `ReviewDeps`) passed through `RunContext`. Tools access GitHub token, PR metadata, and repo info from `ctx.deps`.

**Settings:** `pydantic-settings` `BaseSettings` in `main.py`, populated from env vars (`.env` loaded via `python-dotenv`).

**Webhook security:** `security.py` provides HMAC-SHA256 verification as a FastAPI dependency. The `verify_webhook_signature` dependency imports `get_settings` from `main.py` at call time to avoid circular imports.

## Testing patterns

- Unit tests use `monkeypatch` to mock `httpx.AsyncClient.get` for GitHub API calls
- Agent tests use `agent.override(model=TestModel())` context manager — never set `agent.model` directly
- Integration tests are marked `@pytest.mark.integration` and excluded by default (`addopts = "-m 'not integration'"` in pyproject.toml)
- All tests are async (`asyncio_mode = "auto"`)
- GitHub client mocks in review tests patch `pr_review_agent.github_client.*` functions, not httpx

## Key type suppressions

`# ty: ignore[invalid-return-type]` appears on `result.output` returns from agent runs — this is a known Pydantic AI generic typing limitation.
