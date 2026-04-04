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


async def get_pr_diff(
    owner: str,
    repo: str,
    pr_number: int,
    github_token: str,
) -> str:
    """Fetch the unified diff of a pull request."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text


async def get_file_content(
    owner: str,
    repo: str,
    path: str,
    ref: str,
    github_token: str,
) -> str:
    """Fetch the content of a file at a specific ref (branch/commit SHA)."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.raw+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params={"ref": ref})
        response.raise_for_status()
        return response.text


async def search_code(
    owner: str,
    repo: str,
    query: str,
    github_token: str,
) -> list[CodeSearchResult]:
    """Search for code in a repository."""
    from pr_review_agent.models import CodeSearchResult

    url = f"{GITHUB_API_BASE}/search/code"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"q": f"{query} repo:{owner}/{repo}"}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        items = response.json().get("items", [])
        return [
            CodeSearchResult(
                path=item["path"],
                matched_lines=[
                    frag["fragment"]
                    for frag in item.get("text_matches", [])
                ],
            )
            for item in items
        ]
