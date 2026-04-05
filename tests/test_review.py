from __future__ import annotations

from typing import cast
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
from pr_review_agent.review import _slice_file_content, run_review


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


# --- _slice_file_content unit tests ---


def test_slice_file_content_no_range():
    content = "line1\nline2\nline3\n"
    assert _slice_file_content(content, None, None) == content


def test_slice_file_content_with_range():
    content = "\n".join(f"line{i}" for i in range(1, 11)) + "\n"
    result = _slice_file_content(content, 3, 5)
    assert result.startswith("# Lines 3-5 of 10\n")
    assert "line3" in result
    assert "line5" in result
    assert "line1" not in result
    assert "line6" not in result


def test_slice_file_content_header_format():
    content = "a\nb\nc\n"
    result = _slice_file_content(content, 2, 3)
    first_line = result.splitlines()[0]
    assert first_line == "# Lines 2-3 of 3"


def test_slice_file_content_tool_schema_has_line_range_params():
    from pr_review_agent.review import _FETCH_FILE_CONTENT_TOOL

    props = cast(dict, _FETCH_FILE_CONTENT_TOOL["input_schema"]["properties"])  # type: ignore[index]
    assert "line_start" in props
    assert "line_end" in props


# --- memoization test ---


class CountingFakeGitClient(FakeGitClient):
    def __init__(self):
        self.get_file_content_call_count = 0

    async def get_file_content(
        self, workspace: str, repo_slug: str, path: str, ref: str
    ) -> str:
        self.get_file_content_call_count += 1
        return "\n".join(f"line{i}" for i in range(1, 21)) + "\n"


def _make_two_identical_file_fetches_then_submit(pr_review: PRReview) -> list:
    """Simulate Claude calling fetch_file_content twice with identical args."""

    def _file_fetch_block(tool_id: str):
        b = MagicMock()
        b.type = "tool_use"
        b.name = "fetch_file_content"
        b.id = tool_id
        b.input = {"file_path": "src/main.py"}
        return b

    r1 = MagicMock()
    r1.content = [_file_fetch_block("t1")]
    r1.stop_reason = "tool_use"

    r2 = MagicMock()
    r2.content = [_file_fetch_block("t2")]
    r2.stop_reason = "tool_use"

    return [r1, r2, _make_review_response(pr_review)]


async def test_fetch_file_content_cache_hit(
    sample_pr: GitHubPullRequest,
    sample_repo: GitHubRepo,
    sample_changed_files: list[ChangedFile],
    normal_triage: TriageResult,
    minimal_review: PRReview,
):
    """Repeated fetch_file_content calls with same args hit cache — git client called once."""
    counting_client = CountingFakeGitClient()
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        side_effect=_make_two_identical_file_fetches_then_submit(minimal_review)
    )

    result = await run_review(
        pr=sample_pr,
        repo=sample_repo,
        git_client=counting_client,
        triage_result=normal_triage,
        changed_files=sample_changed_files,
        anthropic_client=mock_client,
    )

    assert isinstance(result, PRReview)
    assert counting_client.get_file_content_call_count == 1
