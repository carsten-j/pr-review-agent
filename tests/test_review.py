from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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
from pr_review_agent.review import run_review


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


@pytest.fixture
def minimal_review() -> PRReview:
    return PRReview(
        summary="Minor feature addition, looks clean.",
        risk_level="low",
        comments=[],
        architectural_observations=[],
        learning_points=[],
        approve=True,
    )


def _make_review_response(pr_review: PRReview):
    """Build a mock response where Claude submits the final review."""
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "submit_review"
    tool_use_block.input = pr_review.model_dump()

    response = MagicMock()
    response.content = [tool_use_block]
    response.stop_reason = "tool_use"
    return response


def _make_tool_call_then_submit(
    tool_name: str, tool_id: str, pr_review: PRReview
) -> list:
    """Simulate one intermediate tool call followed by a submit_review."""
    tool_call_block = MagicMock()
    tool_call_block.type = "tool_use"
    tool_call_block.name = tool_name
    tool_call_block.id = tool_id
    tool_call_block.input = {}

    first_response = MagicMock()
    first_response.content = [tool_call_block]
    first_response.stop_reason = "tool_use"

    return [first_response, _make_review_response(pr_review)]


async def test_general_review_returns_structured_output(
    sample_pr: GitHubPullRequest,
    sample_repo: GitHubRepo,
    sample_changed_files: list[ChangedFile],
    normal_triage: TriageResult,
    minimal_review: PRReview,
):
    """Test that run_review produces a valid PRReview via mocked Anthropic client."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_review_response(minimal_review)
    )

    result = await run_review(
        pr=sample_pr,
        repo=sample_repo,
        git_client=FakeGitClient(),
        triage_result=normal_triage,
        changed_files=sample_changed_files,
        anthropic_client=mock_client,
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
    minimal_review: PRReview,
):
    """Test that security-tagged triage uses the security system prompt."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_review_response(minimal_review)
    )

    result = await run_review(
        pr=sample_pr,
        repo=sample_repo,
        git_client=FakeGitClient(),
        triage_result=security_triage,
        changed_files=sample_changed_files,
        anthropic_client=mock_client,
    )

    assert isinstance(result, PRReview)

    call_kwargs = mock_client.messages.create.call_args.kwargs
    system_text = call_kwargs["system"][0]["text"]
    assert "security specialist" in system_text.lower()
    # reviewer_role persona prefix is NOT prepended for security reviews
    assert "senior-dev" not in system_text


async def test_multi_turn_loop(
    sample_pr: GitHubPullRequest,
    sample_repo: GitHubRepo,
    sample_changed_files: list[ChangedFile],
    normal_triage: TriageResult,
    minimal_review: PRReview,
):
    """Test that the agentic loop advances through an intermediate tool call."""
    responses = _make_tool_call_then_submit(
        "fetch_pr_diff", "tool_abc123", minimal_review
    )
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=responses)

    result = await run_review(
        pr=sample_pr,
        repo=sample_repo,
        git_client=FakeGitClient(),
        triage_result=normal_triage,
        changed_files=sample_changed_files,
        anthropic_client=mock_client,
    )

    assert isinstance(result, PRReview)
    assert mock_client.messages.create.call_count == 2
