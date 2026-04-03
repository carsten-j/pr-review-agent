from __future__ import annotations

import os

import pytest

from pr_review_agent.models import (
    ChangedFile,
    GitHubBranchRef,
    GitHubPullRequest,
    GitHubRepo,
    GitHubUser,
    TriageResult,
)
from pr_review_agent.triage import run_triage

pytestmark = pytest.mark.integration


@pytest.fixture
def sample_pr() -> GitHubPullRequest:
    return GitHubPullRequest(
        number=1,
        title="Fix SQL injection vulnerability in user login",
        body="Parameterized all raw SQL queries in the auth module.",
        state="open",
        user=GitHubUser(login="securitybot", id=99999),
        html_url="https://github.com/example/repo/pull/1",
        diff_url="https://github.com/example/repo/pull/1.diff",
        head=GitHubBranchRef(ref="fix-sqli", sha="aaa111"),
        base=GitHubBranchRef(ref="main", sha="bbb222"),
        created_at="2026-04-03T10:00:00Z",
        updated_at="2026-04-03T10:00:00Z",
    )


async def test_real_triage(
    sample_pr: GitHubPullRequest,
    monkeypatch: pytest.MonkeyPatch,
):
    """Integration test that calls real Anthropic API. Run with: pytest -m integration"""
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    sample_files = [
        ChangedFile(
            filename="src/auth/login.py",
            status="modified",
            additions=15,
            deletions=8,
            changes=23,
        ),
    ]

    async def mock_get_files(owner, repo, pr_number, github_token):
        return sample_files

    monkeypatch.setattr("pr_review_agent.triage.get_pr_changed_files", mock_get_files)

    repo = GitHubRepo(
        full_name="example/repo",
        clone_url="https://github.com/example/repo.git",
        private=False,
    )
    result = await run_triage(pr=sample_pr, repo=repo, github_token="not-used")

    assert isinstance(result, TriageResult)
    assert result.should_review is True
    assert result.priority == "urgent"
    assert result.risk_level in ("high", "critical")
