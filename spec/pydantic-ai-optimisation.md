# Pydantic AI Optimisation Pass

## Context

A review of the codebase against Pydantic AI best practices revealed two non-idiomatic patterns that reduce simplicity and maintainability:

1. **`_register_tools()` helper**: tools were defined as closures inside a helper function called twice — once per agent. Pydantic AI's idiomatic pattern is to define tools as plain functions and pass them via `tools=[...]` in the `Agent` constructor.

2. **`reviewer_role` disconnect**: `REVIEWER_ROLE` was read from the environment at module import time and baked into a module-level f-string. Meanwhile `Settings` already had a `reviewer_role` field that was never wired to the agent. Pydantic AI's idiomatic pattern for dynamic system prompt fragments is `@agent.instructions`, which runs at request time with access to `RunContext[deps]`.

## Changes Made

### Tool registration (`review.py`)

**Before:**
```python
def _register_tools(agent):
    @agent.tool
    async def fetch_pr_diff(ctx): ...
    @agent.tool
    async def fetch_file_content(ctx, file_path): ...
    @agent.tool
    async def search_repo_code(ctx, query): ...

_register_tools(security_review_agent)
_register_tools(general_review_agent)
```

**After:** plain functions passed via `tools=[...]` constructor parameter:
```python
async def fetch_pr_diff(ctx: RunContext[ReviewDeps]) -> str: ...
async def fetch_file_content(ctx: RunContext[ReviewDeps], file_path: str) -> str: ...
async def search_repo_code(ctx: RunContext[ReviewDeps], query: str) -> list[CodeSearchResult]: ...

_REVIEW_TOOLS = [fetch_pr_diff, fetch_file_content, search_repo_code]

security_review_agent = Agent(..., tools=_REVIEW_TOOLS)
general_review_agent  = Agent(..., tools=_REVIEW_TOOLS)
```

### Dynamic reviewer persona (`review.py` + `main.py`)

**Before:**
```python
# review.py — evaluated at import time
REVIEWER_ROLE = os.environ.get("REVIEWER_ROLE", "senior-dev")
GENERAL_REVIEW_SYSTEM_PROMPT = f"You are a {REVIEWER_ROLE} reviewing a pull request. ..."
```

**After:** `reviewer_role` flows from `Settings` → `_run_triage_background` → `run_review()` → `ReviewDeps`, and is injected into the system prompt at request time:
```python
# review.py
@general_review_agent.instructions
def reviewer_persona(ctx: RunContext[ReviewDeps]) -> str:
    return f"You are a {ctx.deps.reviewer_role} reviewing a pull request."
```

### Files modified

| File | Change |
|------|--------|
| `src/pr_review_agent/review.py` | Removed `_register_tools`, switched to `tools=[...]`, added `@instructions`, added `reviewer_role` to `ReviewDeps` and `run_review()` |
| `src/pr_review_agent/main.py` | Passes `settings.reviewer_role` through the background task into `run_review()` |
