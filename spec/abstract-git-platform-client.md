# Plan: Abstract Git Platform Client

## Context

The `github_client.py` module contains 4 standalone async functions that directly call the GitHub API. This tightly couples the entire review pipeline to GitHub. The user wants to introduce an abstraction layer so the system can later support Bitbucket or other platforms, and to align with a dependency injection pattern where tools access the client via `ctx.deps.git_client`.

## Approach

### 1. Create `src/pr_review_agent/git_client.py`

New module with:

- **`GitPlatformClient`** — a `typing.Protocol` defining the abstract interface:
  - `get_pr_changed_files(workspace, repo_slug, pr_id) -> list[ChangedFile]`
  - `get_pr_diff(workspace, repo_slug, pr_id) -> str`
  - `get_file_content(workspace, repo_slug, path, ref) -> str`
  - `search_code(workspace, repo_slug, query) -> list[CodeSearchResult]`

- **`GitHubClient`** — concrete implementation:
  - Stores token on instance, owns a shared `httpx.AsyncClient` for connection pooling
  - Has `close()` method for cleanup
  - Per-method header overrides where needed (e.g., `Accept: application/vnd.github.diff`)

Protocol over ABC — no forced inheritance, test fakes just need matching methods.

### 2. Update `review.py`

- `ReviewDeps.github_token: str` → `git_client: GitPlatformClient`
- Remove `from pr_review_agent import github_client` import
- Tools call `ctx.deps.git_client.get_pr_diff(...)` etc. instead of `github_client.get_pr_diff(...)`
- `run_review()` signature: `github_token: str` → `git_client: GitPlatformClient`

### 3. Update `triage.py`

- `TriageDeps.github_token: str` → `git_client: GitPlatformClient`
- Remove `from pr_review_agent.github_client import get_pr_changed_files`
- `run_triage()` uses `git_client.get_pr_changed_files(...)` directly
- `run_triage()` signature: `github_token: str` → `git_client: GitPlatformClient`

### 4. Update `main.py`

- `_run_triage_background()` creates `GitHubClient(github_token)` at start
- Passes `git_client` to `run_triage()` and `run_review()`
- Calls `git_client.close()` in `finally` block
- Remove `from pr_review_agent.github_client import get_pr_changed_files`

### 5. Delete `github_client.py`

### 6. Update tests

- **`test_github_client.py` → `test_git_client.py`**: Test `GitHubClient` class methods (same httpx mocking approach works since it still uses `httpx.AsyncClient` internally)
- **`test_review.py`**: Replace `_mock_github_tools` monkeypatching with a `FakeGitClient` class passed as `git_client=` — cleaner, no monkeypatch needed
- **`test_triage.py`**: Same — `FakeGitClient` instead of monkeypatching `get_pr_changed_files`
- **`test_review_integration.py`**: Same pattern — `FakeGitClient` with realistic canned data
- **`test_triage_integration.py`**: Same pattern
- **`test_webhook.py`**: No changes needed (patches `_run_triage_background` directly)

## Files to modify

| File | Action |
|------|--------|
| `src/pr_review_agent/git_client.py` | Create |
| `src/pr_review_agent/github_client.py` | Delete |
| `src/pr_review_agent/review.py` | Modify |
| `src/pr_review_agent/triage.py` | Modify |
| `src/pr_review_agent/main.py` | Modify |
| `tests/test_github_client.py` → `tests/test_git_client.py` | Rename + modify |
| `tests/test_review.py` | Modify |
| `tests/test_triage.py` | Modify |
| `tests/test_review_integration.py` | Modify |
| `tests/test_triage_integration.py` | Modify |

## Verification

```bash
uv run pytest -v                          # all unit tests pass
uv run ruff check src/ tests/             # no lint errors
uv run ruff format --check src/ tests/    # formatting OK
uv run ty check src/ tests/               # type check passes
```
