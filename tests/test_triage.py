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
    TriageResult,
)
from pr_review_agent.triage import run_triage


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


def _make_triage_response(triage_result: TriageResult):
    """Build a minimal mock anthropic response for a forced tool_use triage call."""
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "produce_triage_result"
    tool_use_block.input = triage_result.model_dump()

    response = MagicMock()
    response.content = [tool_use_block]
    response.stop_reason = "tool_use"
    return response


async def test_triage_returns_structured_output(
    sample_pr: GitHubPullRequest,
    sample_repo: GitHubRepo,
    sample_changed_files: list[ChangedFile],
):
    """Test that run_triage produces a valid TriageResult via mocked Anthropic client."""
    expected = TriageResult(
        should_review=True,
        priority="normal",
        risk_level="medium",
        reason="Feature addition with moderate complexity",
        tags=["feature"],
    )

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_triage_response(expected)
    )

    result = await run_triage(
        pr=sample_pr,
        repo=sample_repo,
        git_client=FakeGitClient(sample_changed_files),
        anthropic_client=mock_client,
    )

    assert isinstance(result, TriageResult)
    assert result.priority in ("normal", "urgent")
    assert result.risk_level in ("low", "medium", "high", "critical")
    assert isinstance(result.should_review, bool)
    assert isinstance(result.tags, list)
    assert isinstance(result.reason, str)

    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5"
    assert call_kwargs["tool_choice"] == {
        "type": "tool",
        "name": "produce_triage_result",
    }
    assert len(call_kwargs["tools"]) == 1
