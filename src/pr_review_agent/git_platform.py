from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pr_review_agent.models import ChangedFile, CodeSearchResult


class GitPlatformClient(Protocol):
    """Abstract interface for interacting with a git hosting platform."""

    async def get_pr_changed_files(
        self, workspace: str, repo_slug: str, pr_id: int
    ) -> list[ChangedFile]: ...

    async def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str: ...

    async def get_file_content(
        self, workspace: str, repo_slug: str, path: str, ref: str
    ) -> str: ...

    async def search_code(
        self, workspace: str, repo_slug: str, query: str
    ) -> list[CodeSearchResult]: ...


@dataclass
class RepoDeps:
    """Platform-agnostic dependencies for accessing a pull request."""

    git_client: GitPlatformClient
    workspace: str
    repo_slug: str
    pr_id: int
