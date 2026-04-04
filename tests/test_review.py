from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel

from pr_review_agent.models import (
    ChangedFile,
    CodeSearchResult,
    GitHubBranchRef,
    GitHubPullRequest,
    GitHubRepo,
    GitHubUser,
    PRReview,
    TriageResult,
)
from pr_review_agent.review import (
    general_review_agent,
    run_review,
    security_review_agent,
)


class FakeGitClient:
    async def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        return "diff --git a/src/main.py b/src/main.py\n+print('hello')"

    async def get_file_content(
        self, workspace: str, repo_slug: str, path: str, ref: str
    ) -> str:
        return "print('hello')\n"

    async def search_code(
        self, workspace: str, repo_slug: str, query: str
    ) -> list[CodeSearchResult]:
        return []

    async def get_pr_changed_files(
        self, workspace: str, repo_slug: str, pr_id: int
    ) -> list[ChangedFile]:
        return []


@pytest.fixture
def sample_pr() -> GitHubPullRequest:
    return GitHubPullRequest(
        number=42,
        title="Add feature X",
        body="This PR adds feature X",
        state="open",
        user=GitHubUser(login="testuser", id=12345),
        html_url="https://github.com/carsten-j/test/pull/42",
        diff_url="https://github.com/carsten-j/test/pull/42.diff",
        head=GitHubBranchRef(ref="feature-x", sha="abc123"),
        base=GitHubBranchRef(ref="main", sha="def456"),
        created_at="2026-04-03T10:00:00Z",
        updated_at="2026-04-03T10:00:00Z",
    )


@pytest.fixture
def sample_repo() -> GitHubRepo:
    return GitHubRepo(
        full_name="carsten-j/test",
        clone_url="https://github.com/carsten-j/test.git",
        private=False,
    )


@pytest.fixture
def sample_changed_files() -> list[ChangedFile]:
    return [
        ChangedFile(
            filename="src/main.py",
            status="modified",
            additions=10,
            deletions=2,
            changes=12,
        ),
    ]


@pytest.fixture
def normal_triage() -> TriageResult:
    return TriageResult(
        should_review=True,
        priority="normal",
        risk_level="medium",
        reason="Feature addition with moderate complexity",
        tags=["feature"],
    )


@pytest.fixture
def security_triage() -> TriageResult:
    return TriageResult(
        should_review=True,
        priority="urgent",
        risk_level="high",
        reason="Changes to authentication logic",
        tags=["security", "feature"],
    )


async def test_general_review_returns_structured_output(
    sample_pr: GitHubPullRequest,
    sample_repo: GitHubRepo,
    sample_changed_files: list[ChangedFile],
    normal_triage: TriageResult,
):
    """Test that the general review agent produces a valid PRReview."""
    with general_review_agent.override(model=TestModel()):
        result = await run_review(
            pr=sample_pr,
            repo=sample_repo,
            git_client=FakeGitClient(),
            triage_result=normal_triage,
            changed_files=sample_changed_files,
        )

    assert isinstance(result, PRReview)
    assert result.risk_level in ("low", "medium", "high", "critical")
    assert isinstance(result.comments, list)
    assert isinstance(result.approve, bool)
    assert isinstance(result.summary, str)


async def test_security_review_routes_correctly(
    sample_pr: GitHubPullRequest,
    sample_repo: GitHubRepo,
    sample_changed_files: list[ChangedFile],
    security_triage: TriageResult,
):
    """Test that security-tagged triage routes to the security review agent."""
    with security_review_agent.override(model=TestModel()):
        result = await run_review(
            pr=sample_pr,
            repo=sample_repo,
            git_client=FakeGitClient(),
            triage_result=security_triage,
            changed_files=sample_changed_files,
        )

    assert isinstance(result, PRReview)
