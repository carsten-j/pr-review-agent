# PR Review Agent — Phase 3: Code Review Agents

## Context

Triage is working. Now we add the actual code review. Based on the triage result, one of two review agents runs: a security reviewer (if triage tags include "security") or a configurable general reviewer (for everything else). Both use Claude Sonnet 4.5, have tools to fetch diffs/files/search code from GitHub, and produce a structured `PRReview` with line-specific comments. Output is logged only for now.

## Decisions captured

- **Model**: `anthropic:claude-sonnet-4-5` for both reviewers
- **Routing**: security tag → security reviewer, otherwise → general reviewer. Only one runs per PR.
- **Skip**: If `triage.should_review == False`, no review runs
- **Reviewer role**: `REVIEWER_ROLE` env var configures the general reviewer persona (default: "senior-dev")
- **Agent tools**: get_pr_diff, get_file_content, search_code — agent decides what to call
- **Comments**: Diff-line specific (line_start/line_end) — ready for future inline PR comments
- **Output**: Log only
- **Two Agent instances**: Separate `security_review_agent` and `general_review_agent` (share the same tools)

## Files to change

| File | Action |
|------|--------|
| `src/pr_review_agent/models.py` | Add `CodeSearchResult`, `ReviewComment`, `ArchitecturalObservation`, `PRReview` |
| `src/pr_review_agent/github_client.py` | Add `get_pr_diff()`, `get_file_content()`, `search_code()` |
| `src/pr_review_agent/review.py` | **New** — two review agents with tools, `run_review()` |
| `src/pr_review_agent/main.py` | Add `reviewer_role` to Settings, extend background task with review pipeline |
| `.env.example` | Add `REVIEWER_ROLE` |
| `tests/test_github_client.py` | Add tests for three new GitHub client functions |
| `tests/test_review.py` | **New** — unit tests with TestModel |
| `tests/test_review_integration.py` | **New** — integration test |

## Implementation steps

### 1. New models (`models.py`)

```python
class CodeSearchResult(BaseModel):
    path: str
    matched_lines: list[str]

class ReviewComment(BaseModel):
    file_path: str
    line_start: int
    line_end: int | None = None
    severity: Literal["info", "warning", "error", "critical"]
    category: str  # e.g., "security", "naming", "error-handling"
    comment: str
    suggestion: str | None = None

class ArchitecturalObservation(BaseModel):
    pattern: str
    description: str
    affected_files: list[str]

class PRReview(BaseModel):
    summary: str
    risk_level: Literal["low", "medium", "high", "critical"]
    comments: list[ReviewComment]
    architectural_observations: list[ArchitecturalObservation]
    learning_points: list[str]
    approve: bool
```

### 2. New GitHub client functions (`github_client.py`)

Three new async functions following existing pattern:

- `get_pr_diff(owner, repo, pr_number, github_token) -> str` — GET PR with `Accept: application/vnd.github.diff`
- `get_file_content(owner, repo, path, ref, github_token) -> str` — GET `/repos/{owner}/{repo}/contents/{path}?ref={ref}` with raw accept header
- `search_code(owner, repo, query, github_token) -> list[CodeSearchResult]` — GET `/search/code?q={query}+repo:{owner}/{repo}`

### 3. Review agent (`review.py`) — NEW

**ReviewDeps dataclass:**

```python
@dataclass
class ReviewDeps:
    github_token: str
    pr: GitHubPullRequest
    repo: GitHubRepo
    changed_files: list[ChangedFile]
    triage: TriageResult
```

**Two agents** — both Sonnet 4.5, both `output_type=PRReview`, different system prompts:

- `security_review_agent` — fixed security specialist prompt
- `general_review_agent` — prompt incorporates `REVIEWER_ROLE` env var

**Three tools** registered on both agents via `@agent.tool`:

- `get_pr_diff(ctx: RunContext[ReviewDeps]) -> str`
- `get_file_content(ctx: RunContext[ReviewDeps], file_path: str) -> str`
- `search_code(ctx: RunContext[ReviewDeps], query: str) -> list[CodeSearchResult]`

If stacking `@agent.tool` decorators doesn't work, register tools imperatively with `agent.tool(func)`.

**Routing in `run_review()`:**

```python
agent = security_review_agent if "security" in triage_result.tags else general_review_agent
```

### 4. Wire into main (`main.py`)

- Add `reviewer_role: str = Field(default="senior-dev")` to Settings
- Extend `_run_triage_background` to chain the review after triage:
  1. Run triage (existing)
  2. If `should_review == False` → return
  3. Fetch changed_files (re-fetch, simple approach)
  4. Call `run_review(pr, repo, github_token, triage_result, changed_files)`
  5. Log the PRReview result

### 5. Update `.env.example`

Add `REVIEWER_ROLE=senior-dev` with description.

### 6. Tests

**`tests/test_github_client.py`** — add tests for `get_pr_diff`, `get_file_content`, `search_code` (mock httpx)

**`tests/test_review.py`** — use `agent.override(model=TestModel())`:

- Test general review returns valid PRReview
- Test security routing (tags include "security")
- Monkeypatch all GitHub client functions

**`tests/test_review_integration.py`** — `@pytest.mark.integration`:

- Real Anthropic API call with Sonnet 4.5
- Mock GitHub client with realistic canned diff/file data
- Assert PRReview structure and sensible output

**`tests/test_webhook.py`** — no changes needed (background task already mocked)

## Verification

```bash
uv sync
uv run ruff check src/ tests/ scripts/
uv run ruff format --check src/ tests/ scripts/
uv run ty check src/ tests/ scripts/
uv run pytest -v
uv run pytest -m integration  # optional, needs ANTHROPIC_API_KEY
```

Live test: start server + ngrok, open PR on test repo, check logs for triage → review pipeline output.
