"""Triage agent evals: does the triage agent correctly classify PRs?"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic_ai import Agent
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from pr_review_agent.models import (
    ChangedFile,
    CodeSearchResult,
    GitHubBranchRef,
    GitHubPullRequest,
    GitHubRepo,
    GitHubUser,
    TriageResult,
)
from pr_review_agent.triage import run_triage

# ---------------------------------------------------------------------------
# Fake git client
# ---------------------------------------------------------------------------


class FakeGitClient:
    def __init__(self, changed_files: list[ChangedFile]) -> None:
        self._changed_files = changed_files

    async def get_pr_changed_files(
        self, workspace: str, repo_slug: str, pr_id: int
    ) -> list[ChangedFile]:
        return self._changed_files

    async def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        return ""

    async def get_file_content(
        self, workspace: str, repo_slug: str, path: str, ref: str
    ) -> str:
        return ""

    async def search_code(
        self, workspace: str, repo_slug: str, query: str
    ) -> list[CodeSearchResult]:
        return []

    async def post_review(
        self,
        workspace: str,
        repo_slug: str,
        pr_id: int,
        body: str,
        event: str,
        comments: list[dict],
    ) -> None:
        pass


# ---------------------------------------------------------------------------
# Input / output types
# ---------------------------------------------------------------------------


@dataclass
class TriageInputs:
    pr: GitHubPullRequest
    repo: GitHubRepo
    changed_files: list[ChangedFile]
    expect_security: bool = False
    expect_trivial: bool = False


def _make_pr(number: int, title: str, body: str = "") -> GitHubPullRequest:
    return GitHubPullRequest(
        number=number,
        title=title,
        body=body,
        state="open",
        user=GitHubUser(login="testuser", id=1),
        html_url=f"https://github.com/carsten-j/test-repo/pull/{number}",
        diff_url=f"https://github.com/carsten-j/test-repo/pull/{number}.diff",
        head=GitHubBranchRef(ref="feature", sha="abc123"),
        base=GitHubBranchRef(ref="main", sha="def456"),
        created_at="2026-04-05T10:00:00Z",
        updated_at="2026-04-05T10:00:00Z",
    )


SAMPLE_REPO = GitHubRepo(
    full_name="carsten-j/test-repo",
    clone_url="https://github.com/carsten-j/test-repo.git",
    private=False,
)


def _file(filename: str, additions: int = 5, deletions: int = 1) -> ChangedFile:
    return ChangedFile(
        filename=filename,
        status="modified",
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
    )


# ---------------------------------------------------------------------------
# Task function
# ---------------------------------------------------------------------------


async def triage_task(inputs: TriageInputs) -> TriageResult:
    fake_client = FakeGitClient(changed_files=inputs.changed_files)
    return await run_triage(
        pr=inputs.pr,
        repo=inputs.repo,
        git_client=fake_client,
    )


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


@dataclass
class SecurityFlagged(Evaluator[TriageInputs, TriageResult]):
    """If security is expected, priority must be urgent and risk high/critical."""

    def evaluate(
        self, ctx: EvaluatorContext[TriageInputs, TriageResult, None]
    ) -> float:
        if not ctx.inputs.expect_security:
            return 1.0
        priority_ok = ctx.output.priority == "urgent"
        risk_ok = ctx.output.risk_level in ("high", "critical")
        return 1.0 if (priority_ok and risk_ok) else 0.0


@dataclass
class TrivialSkipped(Evaluator[TriageInputs, TriageResult]):
    """If the PR is docs/deps-only, risk must be low."""

    def evaluate(
        self, ctx: EvaluatorContext[TriageInputs, TriageResult, None]
    ) -> float:
        if not ctx.inputs.expect_trivial:
            return 1.0
        return 1.0 if ctx.output.risk_level == "low" else 0.0


@dataclass
class HasReason(Evaluator[TriageInputs, TriageResult]):
    """reason field must be a non-empty string."""

    def evaluate(self, ctx: EvaluatorContext[TriageInputs, TriageResult, None]) -> bool:
        return bool(ctx.output.reason and ctx.output.reason.strip())


_judge_agent: Agent[None, float] = Agent(
    "anthropic:claude-haiku-4-5",
    output_type=float,
    instructions=(
        "Score 0.0–1.0: does the reason field accurately describe the PR's risk "
        "in 1-2 sentences? Reply with only a number between 0.0 and 1.0."
    ),
)


@dataclass
class ReasonQuality(Evaluator[TriageInputs, TriageResult]):
    """LLM judge: does the reason accurately describe the PR's risk?"""

    async def evaluate(
        self, ctx: EvaluatorContext[TriageInputs, TriageResult, None]
    ) -> float:
        result = await _judge_agent.run(
            f"PR title: {ctx.inputs.pr.title}\nReason: {ctx.output.reason}"
        )
        score = result.output  # ty: ignore[invalid-return-type]
        # Clamp in case the model returns out-of-range value
        return max(0.0, min(1.0, float(score)))


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

triage_dataset: Dataset[TriageInputs, TriageResult] = Dataset(
    name="triage-evals",
    cases=[
        Case(
            name="sql_injection",
            inputs=TriageInputs(
                pr=_make_pr(1, "Fix login query", "Fix raw SQL in login handler"),
                repo=SAMPLE_REPO,
                changed_files=[_file("auth/login.py", additions=20, deletions=5)],
                expect_security=True,
            ),
        ),
        Case(
            name="hardcoded_secret",
            inputs=TriageInputs(
                pr=_make_pr(2, "Add API key", "Add API key configuration"),
                repo=SAMPLE_REPO,
                changed_files=[_file("config.py", additions=3, deletions=0)],
                expect_security=True,
            ),
        ),
        Case(
            name="readme_update",
            inputs=TriageInputs(
                pr=_make_pr(3, "Update README", "Fix typos in README"),
                repo=SAMPLE_REPO,
                changed_files=[_file("README.md", additions=5, deletions=3)],
                expect_trivial=True,
            ),
        ),
        Case(
            name="dependency_bump",
            inputs=TriageInputs(
                pr=_make_pr(4, "Bump requests 2.28→2.31"),
                repo=SAMPLE_REPO,
                changed_files=[_file("requirements.txt", additions=1, deletions=1)],
                expect_trivial=True,
            ),
        ),
        Case(
            name="feature_with_tests",
            inputs=TriageInputs(
                pr=_make_pr(5, "Add search endpoint", "Adds /search API endpoint"),
                repo=SAMPLE_REPO,
                changed_files=[
                    _file("search.py", additions=60, deletions=0),
                    _file("test_search.py", additions=40, deletions=0),
                ],
            ),
        ),
    ],
    evaluators=[
        SecurityFlagged(),
        TrivialSkipped(),
        HasReason(),
        ReasonQuality(),
    ],
)


# ---------------------------------------------------------------------------
# pytest integration
# ---------------------------------------------------------------------------


@pytest.mark.evals
async def test_triage_evals() -> None:
    report = await triage_dataset.evaluate(triage_task)
    report.print(include_input=True, include_output=True, include_durations=True)
    # Assert no complete failures on the deterministic evaluators
    for row in report.rows:
        scores = {s.evaluator_name: s.value for s in row.scores}
        assert scores.get("HasReason", 1.0), f"Case {row.case_name!r} has empty reason"
