# PR Review Agent — Phase 2: Triage Agent

## Context

Phase 1 (webhook receiver) is live and working. Now we add a Pydantic AI triage agent that automatically assesses incoming PRs. The agent uses Haiku 4.5 (fast, cheap) to produce a combined triage: should we review, how urgent, how risky. It runs fire-and-forget in the background so the webhook responds immediately. Output is logged only — no PR comments yet.

## Decisions captured

- **Model**: `anthropic:claude-haiku-4-5`
- **Triage model**: Combined (should_review + priority + risk_level + reason + tags)
- **Agent input**: PR metadata from webhook + changed files list from GitHub API
- **Execution**: Fire-and-forget via `asyncio.create_task`
- **Output**: Log only
- **GitHub token**: Separate `GITHUB_TOKEN` env var
- **Testing**: Mock agent with Pydantic AI `TestModel` + one integration test (skipped by default)

## Files changed

| File | Action |
|------|--------|
| `pyproject.toml` | Add `pydantic-ai`, move `httpx` to runtime deps, add `pytest-asyncio` to dev |
| `.env.example` | Add `ANTHROPIC_API_KEY`, `GITHUB_TOKEN` |
| `src/pr_review_agent/models.py` | Add `ChangedFile`, `TriageResult` |
| `src/pr_review_agent/github_client.py` | **New** — fetch PR changed files from GitHub API |
| `src/pr_review_agent/triage.py` | **New** — Pydantic AI agent + `run_triage()` |
| `src/pr_review_agent/main.py` | Add Settings fields, background triage task |
| `tests/test_webhook.py` | Update fixture env vars, mock background task |
| `tests/test_github_client.py` | **New** — mock httpx tests |
| `tests/test_triage.py` | **New** — TestModel-based unit tests |
| `tests/test_triage_integration.py` | **New** — real API integration test |

## Implementation details

### Triage agent (`triage.py`)

- `TriageDeps` dataclass: github_token, pr, repo, changed_files
- Module-level `triage_agent = Agent("anthropic:claude-haiku-4-5", deps_type=TriageDeps, output_type=TriageResult)`
- System prompt instructs agent on how to classify: should_review, priority (normal/urgent), risk_level (low/medium/high/critical), reason, tags
- `_format_user_prompt(deps)` builds readable text from PR metadata + changed files list
- `run_triage(pr, repo, github_token)` fetches changed files, builds deps, runs agent, returns TriageResult
- No agent tools — all data pre-fetched and passed in prompt (one LLM call)

### GitHub client (`github_client.py`)

Single async function:
```python
async def get_pr_changed_files(owner, repo, pr_number, github_token) -> list[ChangedFile]
```
- Calls `GET /repos/{owner}/{repo}/pulls/{pr_number}/files`
- Uses `httpx.AsyncClient` with Bearer auth

### Webhook integration (`main.py`)

- Webhook handler returns 200 immediately
- Triage runs in background via `asyncio.create_task`
- Result logged with structured fields (should_review, priority, risk_level, tags, reason)
- Exceptions caught and logged, never crash the server

### Testing strategy

- **Unit tests**: Mock Pydantic AI agent with `TestModel`, mock GitHub API with monkeypatched httpx
- **Integration test**: Marked `@pytest.mark.integration`, skipped by default, calls real Anthropic API

## Verification

```bash
uv run ruff check src/ tests/ scripts/
uv run ruff format --check src/ tests/ scripts/
uv run ty check src/ tests/ scripts/
uv run pytest -v
uv run pytest -m integration  # optional, needs ANTHROPIC_API_KEY
```
