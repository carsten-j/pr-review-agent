from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel

from pr_review_agent.models import (
    ChangedFile,
    GitHubBranchRef,
    GitHubPullRequest,
    GitHubRepo,
    GitHubUser,
    TriageResult,
)
from pr_review_agent.triage import run_triage, triage_agent


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


async def test_triage_returns_structured_output(
    sample_pr: GitHubPullRequest,
    sample_repo: GitHubRepo,
    sample_changed_files: list[ChangedFile],
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that the triage agent produces a valid TriageResult using TestModel."""

    async def mock_get_files(owner, repo, pr_number, github_token):
        return sample_changed_files

    monkeypatch.setattr("pr_review_agent.triage.get_pr_changed_files", mock_get_files)

    with triage_agent.override(model=TestModel()):
        result = await run_triage(
            pr=sample_pr,
            repo=sample_repo,
            github_token="fake-token",
        )

    assert isinstance(result, TriageResult)
    assert result.priority in ("normal", "urgent")
    assert result.risk_level in ("low", "medium", "high", "critical")
    assert isinstance(result.should_review, bool)
    assert isinstance(result.tags, list)
    assert isinstance(result.reason, str)
