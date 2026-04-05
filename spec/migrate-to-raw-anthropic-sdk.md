# Plan: Migrate from Pydantic AI to Raw Anthropic SDK

## Context

The PR review agent currently uses Pydantic AI as its agent framework. The goal is to replace it with the official `anthropic` Python SDK, removing the abstraction layer and owning the tool-use agentic loop directly. This gives full control over retries, context, and the loop — at the cost of more explicit boilerplate.

The pipeline and all business logic stay the same: FastAPI webhook → triage (Haiku) → review (Sonnet). Only the AI wiring changes.

## What changes vs what stays

**Unchanged**: `models.py`, `git_platform.py`, `github_client.py`, `security.py`, `test_webhook.py`, `test_git_client.py`

**Rewritten**: `triage.py`, `review.py`, `tests/test_triage.py`, `tests/test_review.py`

**Small edits**: `main.py` (one line + client instantiation), `pyproject.toml` (dependency swap), `tests/test_triage_integration.py`, `tests/test_review_integration.py` (add `anthropic_client` arg)

## Key patterns

### Structured output via forced tool call

Both agents produce structured output by defining a "submit" tool whose `input_schema` is the Pydantic model's JSON Schema, then forcing Claude to call it:

```python
_TRIAGE_OUTPUT_TOOL = {
    "name": "produce_triage_result",
    "description": "Produce the structured triage assessment.",
    "input_schema": TriageResult.model_json_schema(),
}

response = await client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=1024,
    system=TRIAGE_SYSTEM_PROMPT,
    tools=[_TRIAGE_OUTPUT_TOOL],
    tool_choice={"type": "tool", "name": "produce_triage_result"},
    messages=[{"role": "user", "content": user_prompt}],
)
tool_use = next(b for b in response.content if b.type == "tool_use")
return TriageResult.model_validate(tool_use.input)
```

`tool_choice={"type": "tool", "name": "..."}` forces a single structured call with no loop.

### Agentic loop (review)

The review agent loops until Claude calls `submit_review`:

```python
async def _run_review_loop(...) -> PRReview:
    messages = [{"role": "user", "content": user_prompt}]
    for iteration in range(max_iterations):
        response = await client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8192,
            system=system,
            tools=all_tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "max_tokens":
            raise RuntimeError("Review response truncated — increase max_tokens")
        if response.stop_reason == "end_turn":
            raise RuntimeError("Agent stopped without submitting review")

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "submit_review":
                return PRReview.model_validate(block.input)
            result = await tool_executors[block.name](block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Exceeded max_iterations={max_iterations}")
```

### Tools as closures (replaces RunContext)

Instead of `ctx: RunContext[ReviewDeps]`, tools are closures over the call-scoped context:

```python
async def run_review(pr, repo, git_client, ..., anthropic_client):
    workspace, repo_slug = repo.full_name.split("/", 1)

    async def _fetch_pr_diff(_input: dict) -> str:
        return await git_client.get_pr_diff(workspace, repo_slug, pr.number)

    async def _fetch_file_content(input_: dict) -> str:
        return await git_client.get_file_content(
            workspace, repo_slug, input_["file_path"], pr.head.sha
        )

    async def _search_repo_code(input_: dict) -> str:
        results = await git_client.search_code(workspace, repo_slug, input_["query"])
        return json.dumps([r.model_dump() for r in results])

    tool_executors = {
        "fetch_pr_diff": _fetch_pr_diff,
        "fetch_file_content": _fetch_file_content,
        "search_repo_code": _search_repo_code,
    }
```

### Dynamic reviewer persona (replaces @agent.instructions)

```python
if not is_security:
    system = f"You are a {reviewer_role} reviewing a pull request.\n\n{GENERAL_REVIEW_SYSTEM_PROMPT}"
else:
    system = SECURITY_REVIEW_SYSTEM_PROMPT
```

### Client injection (testability)

`AsyncAnthropic` client is created once in `main.py` and passed to both `run_triage` and `run_review`. Tests inject a `MagicMock` instead. No global state.

```python
# main.py
anthropic_client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from env
triage_result = await run_triage(pr=pr, repo=repo, git_client=git_client, anthropic_client=anthropic_client)
review = await run_review(..., anthropic_client=anthropic_client)
```

## Observability

Replace `logfire.instrument_pydantic_ai()` with `logfire.instrument_anthropic()` — one line change, same Logfire project, no token changes needed.

What `instrument_anthropic()` captures: every `messages.create()` call as a span with model, tokens, latency. What it **does not** capture automatically: individual tool executions between API turns (Pydantic AI wired those). To make tool calls visible, add `with logfire.span("tool: fetch_pr_diff")` inside `_run_review_loop`. This is optional — the API call spans already show which tools Claude requested in the request/response payload.

The `logfire.span("review PR {repo}#{pr_number}", ...)` root span in `main.py` is unaffected.

## Changes by file

### `pyproject.toml`
- Remove `"pydantic-ai>=0.2.0"`
- Add `"anthropic>=0.50.0"` (stable `AsyncAnthropic`, `tool_choice` support)
- Run `uv sync`

### `src/pr_review_agent/main.py`
- `import anthropic` at top
- `logfire.instrument_pydantic_ai()` → `logfire.instrument_anthropic()`
- In `_run_triage_background`: create `anthropic_client = AsyncAnthropic()` after `git_client`
- Pass `anthropic_client=anthropic_client` to `run_triage()` and `run_review()`
- `finally`: only `git_client.close()` needed (Anthropic client is stateless)

### `src/pr_review_agent/triage.py`
- Remove: `from pydantic_ai import Agent`, `triage_agent = Agent(...)`
- Add: `from anthropic import AsyncAnthropic`
- Add: `_TRIAGE_OUTPUT_TOOL` dict (schema from `TriageResult.model_json_schema()`)
- `TriageDeps`, `TRIAGE_SYSTEM_PROMPT`, `_format_user_prompt` — **unchanged**
- `run_triage` signature: add `anthropic_client: AsyncAnthropic` parameter
- `run_triage` body: single `messages.create()` call with forced tool_choice, return `TriageResult.model_validate(tool_use.input)`

### `src/pr_review_agent/review.py`
- Remove: `from pydantic_ai import Agent, RunContext`, both `Agent(...)` globals, `@general_review_agent.instructions`
- Add: `from anthropic import AsyncAnthropic`, `import json`
- Add: four tool schema dicts (`_FETCH_PR_DIFF_TOOL`, `_FETCH_FILE_CONTENT_TOOL`, `_SEARCH_REPO_CODE_TOOL`, `_SUBMIT_REVIEW_TOOL`)
- Add: `_run_review_loop()` helper function
- `ReviewDeps`, `SECURITY_REVIEW_SYSTEM_PROMPT`, `GENERAL_REVIEW_SYSTEM_PROMPT`, `_format_review_prompt` — **unchanged**
- `run_review` signature: add `anthropic_client: AsyncAnthropic` parameter
- `run_review` body: build closures, build system prompt string, call `_run_review_loop()`
- `search_repo_code` return serialised as `json.dumps([r.model_dump() for r in results])`

### `tests/test_triage.py`
- Remove: `from pydantic_ai.models.test import TestModel`, `triage_agent.override()` context manager
- Add: `from unittest.mock import AsyncMock, MagicMock`
- Add: `_make_triage_response(triage_result)` helper — returns a mock with `content=[tool_use_block]`
- Tests pass `mock_client` to `run_triage(..., anthropic_client=mock_client)`
- Assert `mock_client.messages.create` called with correct `model` and `tool_choice`

### `tests/test_review.py`
- Remove: Pydantic AI TestModel machinery
- Add: `_make_review_response(pr_review)` and `_make_tool_call_then_submit(tool_name, tool_id, pr_review)` helpers
- Add: `test_multi_turn_loop` — verifies loop advances through an intermediate tool call (new test, did not exist before)
- `test_security_review_routes_correctly` asserts `"security specialist" in call_kwargs["system"].lower()` instead of checking which agent object ran
- Tests pass `mock_client` to `run_review(..., anthropic_client=mock_client)`

### `tests/test_triage_integration.py` and `tests/test_review_integration.py`
- Add `from anthropic import AsyncAnthropic`
- Add `client = AsyncAnthropic()` in test body
- Pass `anthropic_client=client` to `run_triage()` / `run_review()`
- All `@pytest.mark.integration` markers and fixture definitions unchanged

## Risks

**`PRReview.model_json_schema()` uses `$defs`** — nested models (`ReviewComment`, `ArchitecturalObservation`) emit `$ref` references. The Anthropic API accepts this. Verify in integration tests.

**`max_tokens=8192` for review** — a detailed review with many comments approaches 3000–4000 tokens. If Claude hits the limit, `stop_reason == "max_tokens"` and the partial tool input causes a `ValidationError`. The loop explicitly checks and raises a descriptive error rather than looping with a broken context.

**Loop termination** — three guards: `max_iterations=20`, `stop_reason == "end_turn"` check, `stop_reason == "max_tokens"` check. Normal reviews run in 3–8 turns.

## Verification

```bash
uv sync                          # installs anthropic, removes pydantic-ai
uv run pytest -v                 # unit tests — no API key needed
uv run ruff check src/ tests/ scripts/
uv run ty check src/ tests/ scripts/
uv run pytest -m integration     # requires ANTHROPIC_API_KEY
uv run uvicorn pr_review_agent.main:app --reload
uv run python scripts/simulate_webhook.py   # end-to-end smoke test
```

After running the simulation, open Logfire — the `review PR ...` root span should appear with `instrument_anthropic()` child spans for each `messages.create()` call.
