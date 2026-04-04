from __future__ import annotations

import logging

import httpx

from pr_review_agent.models import ChangedFile, CodeSearchResult

logger = logging.getLogger(__name__)

DEFAULT_GITHUB_API_BASE = "https://api.github.com"


class GitHubClient:
    """GitHub implementation of GitPlatformClient."""

    def __init__(self, token: str, *, base_url: str = DEFAULT_GITHUB_API_BASE) -> None:
        self._token = token
        self._base_url = base_url
        self._http = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def get_pr_changed_files(
        self, workspace: str, repo_slug: str, pr_id: int
    ) -> list[ChangedFile]:
        url = f"{self._base_url}/repos/{workspace}/{repo_slug}/pulls/{pr_id}/files"
        response = await self._http.get(url)
        response.raise_for_status()
        return [ChangedFile.model_validate(f) for f in response.json()]

    async def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        url = f"{self._base_url}/repos/{workspace}/{repo_slug}/pulls/{pr_id}"
        response = await self._http.get(
            url, headers={"Accept": "application/vnd.github.diff"}
        )
        response.raise_for_status()
        return response.text

    async def get_file_content(
        self, workspace: str, repo_slug: str, path: str, ref: str
    ) -> str:
        url = f"{self._base_url}/repos/{workspace}/{repo_slug}/contents/{path}"
        response = await self._http.get(
            url,
            headers={"Accept": "application/vnd.github.raw+json"},
            params={"ref": ref},
        )
        response.raise_for_status()
        return response.text

    async def search_code(
        self, workspace: str, repo_slug: str, query: str
    ) -> list[CodeSearchResult]:
        url = f"{self._base_url}/search/code"
        params = {"q": f"{query} repo:{workspace}/{repo_slug}"}
        response = await self._http.get(url, params=params)
        response.raise_for_status()
        items = response.json().get("items", [])
        return [
            CodeSearchResult(
                path=item["path"],
                matched_lines=[
                    frag["fragment"] for frag in item.get("text_matches", [])
                ],
            )
            for item in items
        ]
