from __future__ import annotations

import logging

import httpx

from pr_review_agent.models import ChangedFile, CodeSearchResult

logger = logging.getLogger(__name__)

DEFAULT_BITBUCKET_API_BASE = "https://api.bitbucket.org/2.0"


class BitbucketClient:
    """Bitbucket Cloud implementation of GitPlatformClient."""

    def __init__(
        self,
        username: str,
        app_password: str,
        *,
        base_url: str = DEFAULT_BITBUCKET_API_BASE,
    ) -> None:
        self._base_url = base_url
        self._http = httpx.AsyncClient(
            auth=httpx.BasicAuth(username, app_password),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def get_pr_changed_files(
        self, workspace: str, repo_slug: str, pr_id: int
    ) -> list[ChangedFile]:
        url = f"{self._base_url}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/diffstat"
        response = await self._http.get(url)
        response.raise_for_status()
        items = response.json().get("values", [])
        return [
            ChangedFile(
                filename=(item.get("new") or item.get("old") or {}).get("path", ""),
                status=item.get("status", "modified"),
                additions=item.get("lines_added", 0),
                deletions=item.get("lines_removed", 0),
                changes=item.get("lines_added", 0) + item.get("lines_removed", 0),
            )
            for item in items
        ]

    async def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        url = f"{self._base_url}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/diff"
        response = await self._http.get(url)
        response.raise_for_status()
        return response.text

    async def get_file_content(
        self, workspace: str, repo_slug: str, path: str, ref: str
    ) -> str:
        url = f"{self._base_url}/repositories/{workspace}/{repo_slug}/src/{ref}/{path}"
        response = await self._http.get(url)
        response.raise_for_status()
        return response.text

    async def search_code(
        self, workspace: str, repo_slug: str, query: str
    ) -> list[CodeSearchResult]:
        # Bitbucket code search requires a Premium workspace plan.
        # On Standard/Free plans the endpoint returns 404 or 403.
        # We degrade gracefully — return empty results so the review
        # agent can continue without code search context.
        url = f"{self._base_url}/repositories/{workspace}/{repo_slug}/search/code"
        params = {"search_query": query}
        response = await self._http.get(url, params=params)
        if response.status_code in (403, 404):
            logger.warning(
                "Bitbucket code search unavailable for %s/%s (status %d). "
                "Upgrade to a Premium workspace to enable code search.",
                workspace,
                repo_slug,
                response.status_code,
            )
            return []
        response.raise_for_status()
        items = response.json().get("values", [])
        return [
            CodeSearchResult(
                path=item.get("file", {}).get("path", ""),
                matched_lines=[
                    line.get("line", "")
                    for match in item.get("content_matches", [])
                    for line in match.get("lines", [])
                    if line.get("line_type") == "context"
                ],
            )
            for item in items
        ]

    async def post_review(
        self,
        workspace: str,
        repo_slug: str,
        pr_id: int,
        body: str,
        event: str,
        comments: list[dict],
    ) -> None:
        base = f"{self._base_url}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"

        # Post overall summary comment
        summary_response = await self._http.post(
            f"{base}/comments",
            json={"content": {"raw": body}},
        )
        summary_response.raise_for_status()

        # Post inline comments
        for comment in comments:
            inline_response = await self._http.post(
                f"{base}/comments",
                json={
                    "content": {"raw": comment["body"]},
                    "inline": {"path": comment["path"], "to": comment["line"]},
                },
            )
            inline_response.raise_for_status()

        # Approve or request changes
        if event == "APPROVE":
            approve_response = await self._http.post(f"{base}/approve")
            approve_response.raise_for_status()
        else:
            changes_response = await self._http.post(f"{base}/request-changes")
            changes_response.raise_for_status()
