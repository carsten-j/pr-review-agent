# Logfire Observability

## Goals

1. **Debug** — see exactly which tools agents call, what they send, what comes back
2. **Cost tracking** — token usage per agent run, per PR
3. **Audit trail** — high-level: for any PR, was it reviewed, what did triage decide, approve/reject?

Scope: **Pydantic AI agents only** (no httpx/GitHub API instrumentation). Gracefully inactive when `LOGFIRE_TOKEN` is not set.

## What Logfire captures automatically via `instrument_pydantic_ai()`

- Every agent run as a trace (triage + each review)
- All tool calls with inputs and outputs (`fetch_pr_diff`, `fetch_file_content`, `search_repo_code`)
- Model requests and responses (full prompt + completion)
- Token usage per request

## Changes made

### `pyproject.toml`

Added explicit logfire dependency (already bundled by pydantic-ai, declared to pin intent):

```toml
"logfire>=3.0",
```

### `.env.example`

Documented the optional write token:

```bash
# Logfire observability (https://logfire.pydantic.dev)
# Write token for the pr-review-agent project
# Generate at: logfire.pydantic.dev → project → Settings → Write tokens
# Leave unset to disable tracing (app runs normally without it)
# LOGFIRE_TOKEN=your-write-token-here
```

### `src/pr_review_agent/main.py`

Two additions — no changes to any agent file:

**a) Module level, after `load_dotenv()`:**
```python
import logfire

logfire.configure()              # reads LOGFIRE_TOKEN from env; no-op if absent
logfire.instrument_pydantic_ai() # instruments all agents globally
```

**b) PR-level span wrapping `_run_triage_background`** — groups triage + review under one searchable trace:
```python
with logfire.span(
    "review PR {repo}#{pr_number}",
    repo=repo.full_name,
    pr_number=pr.number,
    pr_title=pr.title,
    pr_author=pr.user.login,
):
    # triage → review pipeline
```

The span attributes (`repo`, `pr_number`, `pr_title`, `pr_author`) are searchable fields in the Logfire UI.

## Activation

1. Create a **write token** at logfire.pydantic.dev → project `pr-review-agent` → Settings → Write tokens
2. Add `LOGFIRE_TOKEN=<your-token>` to `.env`
3. `uv run uvicorn pr_review_agent.main:app --reload`
4. Trigger a run via `uv run python scripts/simulate_webhook.py`
5. Open logfire.pydantic.dev — you should see a `review PR ...` root span containing child spans for the triage and review agent runs, tool calls, and token usage

Without `LOGFIRE_TOKEN` the app runs normally with no overhead.
