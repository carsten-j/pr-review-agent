from __future__ import annotations

import logging

import httpx

from pr_review_agent.models import ChangedFile

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


async def get_pr_changed_files(
    owner: str,
    repo: str,
    pr_number: int,
    github_token: str,
) -> list[ChangedFile]:
    """Fetch the list of changed files for a pull request from GitHub API."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/files"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return [ChangedFile.model_validate(f) for f in response.json()]
