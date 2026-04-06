from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel

from pr_review_agent.models import (
    ChangedFile,
    CodeSearchResult,
    PRReview,
    PullRequestInfo,
    RepoInfo,
    ReviewComment,
    TriageResult,
)
from pr_review_agent.review import (
    general_review_agent,
    post_review_comments,
    run_review,
    security_review_agent,
)


class FakeGitClient:
    def __init__(self) -> None:
        self.posted_reviews: list[dict] = []

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

    async def post_review(
        self,
        workspace: str,
        repo_slug: str,
        pr_id: int,
        body: str,
        event: str,
        comments: list[dict],
    ) -> None:
        self.posted_reviews.append(
            {
                "workspace": workspace,
                "repo_slug": repo_slug,
                "pr_id": pr_id,
                "body": body,
                "event": event,
                "comments": comments,
            }
        )


@pytest.fixture
def sample_pr() -> PullRequestInfo:
    return PullRequestInfo(
        number=42,
        title="Add feature X",
        body="This PR adds feature X",
        author_login="testuser",
        html_url="https://github.com/carsten-j/test/pull/42",
        head_branch="feature-x",
        head_sha="abc123",
        base_branch="main",
    )


@pytest.fixture
def sample_repo() -> RepoInfo:
    return RepoInfo(full_name="carsten-j/test", is_private=False)


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
    sample_pr: PullRequestInfo,
    sample_repo: RepoInfo,
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
    sample_pr: PullRequestInfo,
    sample_repo: RepoInfo,
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


async def test_post_review_comments_approve():
    """post_review_comments maps an approving PRReview to APPROVE event with correct comment shape."""
    review = PRReview(
        summary="Looks good",
        risk_level="low",
        approve=True,
        comments=[
            ReviewComment(
                file_path="src/main.py",
                line_start=10,
                severity="warning",
                category="naming",
                comment="Use snake_case here",
                suggestion="my_variable = 1",
            )
        ],
    )
    client = FakeGitClient()
    await post_review_comments(client, "owner", "repo", 42, review)

    assert len(client.posted_reviews) == 1
    call = client.posted_reviews[0]
    assert call["workspace"] == "owner"
    assert call["repo_slug"] == "repo"
    assert call["pr_id"] == 42
    assert call["body"] == "Looks good"
    assert call["event"] == "APPROVE"
    assert len(call["comments"]) == 1
    c = call["comments"][0]
    assert c["path"] == "src/main.py"
    assert c["line"] == 10
    assert "[warning]" in c["body"]
    assert "naming" in c["body"]
    assert "Use snake_case here" in c["body"]
    assert "my_variable = 1" in c["body"]


async def test_post_review_comments_request_changes():
    """post_review_comments maps a non-approving PRReview to REQUEST_CHANGES event."""
    review = PRReview(
        summary="Needs work",
        risk_level="high",
        approve=False,
        comments=[
            ReviewComment(
                file_path="src/auth.py",
                line_start=5,
                severity="critical",
                category="security",
                comment="SQL injection risk",
                suggestion=None,
            )
        ],
    )
    client = FakeGitClient()
    await post_review_comments(client, "owner", "repo", 7, review)

    call = client.posted_reviews[0]
    assert call["event"] == "REQUEST_CHANGES"
    c = call["comments"][0]
    assert "Suggestion" not in c["body"]


async def test_post_review_comments_no_comments():
    """post_review_comments works when there are no inline comments."""
    review = PRReview(
        summary="No issues found",
        risk_level="low",
        approve=True,
        comments=[],
    )
    client = FakeGitClient()
    await post_review_comments(client, "owner", "repo", 1, review)

    call = client.posted_reviews[0]
    assert call["comments"] == []
    assert call["event"] == "APPROVE"
