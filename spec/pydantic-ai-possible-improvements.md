# Suggestions from Pydantic AI Docs (main branch — Pydantic AI)

## Context

The `main` branch uses Pydantic AI throughout (`triage.py`, `review.py`). The Pydantic AI docs surface several built-in features and patterns that the current codebase doesn't yet use. Suggestions are ranked by impact.

---

## 1. Post Reviews Back to GitHub (Highest Impact)

**What:** The pipeline logs reviews but never posts them to GitHub. The `GitPlatformClient` protocol is missing a `post_review()` method.

**How:**
- Add `post_review(workspace, repo_slug, pr_id, review: PRReview) -> None` to `git_platform.py`
- Implement in `github_client.py` via `POST /repos/{owner}/{repo}/pulls/{pr_id}/reviews`:
  - `body` = `PRReview.summary`
  - `event` = `"APPROVE"` if `approve` else `"REQUEST_CHANGES"`
  - `comments` = inline `ReviewComment` items (file, position, body)
- Call after `run_review()` in `main.py`

**Files:** `git_platform.py`, `github_client.py`, `main.py`, `tests/test_git_client.py`

---

## 2. Handle `synchronize` Action (Re-review on New Commits)

**What:** Only `opened` PRs are reviewed. Pydantic AI docs emphasise multi-turn conversation continuation — the same principle: re-review when new commits are pushed.

**How:**
- In `main.py`, extend the action filter: `action in {"opened", "synchronize", "reopened"}`
- No agent changes needed

**Files:** `main.py`, `tests/test_webhook.py`

---

## 3. Usage Tracking (Token Cost per PR)

**What:** Pydantic AI exposes usage metadata via `result.usage()` on every `agent.run()` call. The pipeline fires multiple agent runs (triage + review) with no token tracking.

**How:**
- After each `agent.run()` call, log `result.usage()` (request_tokens, response_tokens, total_tokens) as Logfire span attributes
- Sum across triage + review and attach to the top-level `logfire.span()` in `_run_triage_background()`

**Files:** `triage.py`, `review.py`, `main.py`

---

## 4. Retry Configuration

**What:** Pydantic AI supports `retries=N` on `Agent()` and `ModelRetry` in tools. Currently any transient API failure silently kills the entire PR pipeline.

**How:**
- Add `retries=2` to both `triage_agent` and `general_review_agent` / `security_review_agent`
- In tool functions (`fetch_pr_diff`, `fetch_file_content`, `search_repo_code`), raise `ModelRetry` on HTTP errors so the agent can retry with context

**Files:** `triage.py`, `review.py`

---

## 5. Streaming Review Output

**What:** Pydantic AI provides `agent.run_stream()` for streaming structured output. Long diffs currently mean 30–60s of silence before any output. Streaming enables incremental Logfire spans and faster time-to-first-output.

**How:**
- Replace `await agent.run(...)` in `run_review()` with `async with agent.run_stream(...) as result:`
- Use `await result.stream_text(delta=True)` to emit chunks to Logfire
- Collect final output via `await result.get_output()`

**Files:** `review.py`

---

## 6. Parallel Security + General Review for Critical PRs

**What:** Pydantic AI multi-agent docs show agents calling other agents. Currently security and general review are mutually exclusive. For `risk_level == "critical"`, running both in parallel gives fuller coverage.

**How:**
- In `run_review()`, detect `risk_level == "critical"` and run both agents concurrently via `asyncio.gather(security_review_agent.run(...), general_review_agent.run(...))`
- Add `merge_reviews(a: PRReview, b: PRReview) -> PRReview` to `models.py`: combine `comments`, `architectural_observations`, `learning_points`; take the more conservative `approve`

**Files:** `review.py`, `models.py`

---

## 7. Concurrency Limiting

**What:** Pydantic AI documents `ConcurrencyLimit` to cap simultaneous agent runs. A burst of PRs (e.g. merge train) would fire unlimited parallel Anthropic API calls.

**How:**
- Add `max_concurrent_reviews: int = 3` to `Settings`
- Create `asyncio.Semaphore(settings.max_concurrent_reviews)` at app startup
- Acquire before entering `_run_triage_background()`

**Files:** `main.py`

---

## Verification

1. **Post reviews:** `scripts/simulate_webhook.py` → check GitHub PR for posted review comment
2. **synchronize:** Simulate webhook with `action=synchronize` → confirm triage runs
3. **Usage tracking:** Integration test → verify Logfire span has token count attributes
4. **Retries:** Mock tool to raise `httpx.TimeoutException` → verify `ModelRetry` triggers re-attempt
5. **Streaming:** Integration test → verify incremental log output during review
6. **Parallel review:** Integration test with `risk_level=critical` → verify merged output has comments from both reviewers
7. **Concurrency:** Fire 10 simultaneous webhooks → verify semaphore caps active tasks at 3

---

## Recommendation

Start with **#1 (post to GitHub)** — the pipeline produces no user-visible output in GitHub today. Then **#2 (synchronize)** as a one-liner. The rest can follow in order of priority.
