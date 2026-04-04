from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel

from pr_review_agent.models import (
    ChangedFile,
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


def _mock_github_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch all GitHub client functions used by review tools."""

    async def mock_get_pr_diff(owner, repo, pr_number, github_token):
        return "diff --git a/src/main.py b/src/main.py\n+print('hello')"

    async def mock_get_file_content(owner, repo, path, ref, github_token):
        return "print('hello')\n"

    async def mock_search_code(owner, repo, query, github_token):
        return []

    monkeypatch.setattr("pr_review_agent.github_client.get_pr_diff", mock_get_pr_diff)
    monkeypatch.setattr(
        "pr_review_agent.github_client.get_file_content", mock_get_file_content
    )
    monkeypatch.setattr("pr_review_agent.github_client.search_code", mock_search_code)


async def test_general_review_returns_structured_output(
    sample_pr: GitHubPullRequest,
    sample_repo: GitHubRepo,
    sample_changed_files: list[ChangedFile],
    normal_triage: TriageResult,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that the general review agent produces a valid PRReview."""
    _mock_github_tools(monkeypatch)

    with general_review_agent.override(model=TestModel()):
        result = await run_review(
            pr=sample_pr,
            repo=sample_repo,
            github_token="fake-token",
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
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that security-tagged triage routes to the security review agent."""
    _mock_github_tools(monkeypatch)

    with security_review_agent.override(model=TestModel()):
        result = await run_review(
            pr=sample_pr,
            repo=sample_repo,
            github_token="fake-token",
            triage_result=security_triage,
            changed_files=sample_changed_files,
        )

    assert isinstance(result, PRReview)
