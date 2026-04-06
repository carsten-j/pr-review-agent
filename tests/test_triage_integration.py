from __future__ import annotations

import os

import pytest

from pr_review_agent.models import (
    ChangedFile,
    CodeSearchResult,
    PullRequestInfo,
    RepoInfo,
    TriageResult,
)
from pr_review_agent.triage import run_triage

pytestmark = pytest.mark.integration


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


@pytest.fixture
def sample_pr() -> PullRequestInfo:
    return PullRequestInfo(
        number=1,
        title="Fix SQL injection vulnerability in user login",
        body="Parameterized all raw SQL queries in the auth module.",
        author_login="securitybot",
        html_url="https://github.com/example/repo/pull/1",
        head_branch="fix-sqli",
        head_sha="aaa111",
        base_branch="main",
    )


async def test_real_triage(
    sample_pr: PullRequestInfo,
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

    repo = RepoInfo(full_name="example/repo", is_private=False)
    result = await run_triage(
        pr=sample_pr, repo=repo, git_client=FakeGitClient(sample_files)
    )

    assert isinstance(result, TriageResult)
    assert result.should_review is True
    assert result.priority == "urgent"
    assert result.risk_level in ("high", "critical")
