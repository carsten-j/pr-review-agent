from __future__ import annotations

import httpx
import pytest

from pr_review_agent.github_client import (
    get_file_content,
    get_pr_changed_files,
    get_pr_diff,
    search_code,
)

SAMPLE_FILES_RESPONSE = [
    {
        "filename": "src/main.py",
        "status": "modified",
        "additions": 10,
        "deletions": 2,
        "changes": 12,
        "patch": "@@ -1,5 +1,15 @@...",
    },
    {
        "filename": "README.md",
        "status": "modified",
        "additions": 3,
        "deletions": 1,
        "changes": 4,
    },
]

SAMPLE_DIFF = "diff --git a/src/main.py b/src/main.py\n+print('hello')"

SAMPLE_SEARCH_RESPONSE = {
    "items": [
        {
            "path": "src/main.py",
            "text_matches": [{"fragment": "def hello():"}],
        }
    ]
}


@pytest.fixture
def mock_github_api(monkeypatch: pytest.MonkeyPatch):
    """Mock httpx.AsyncClient.get to return sample files."""

    async def mock_get(self, url, **kwargs):
        return httpx.Response(
            status_code=200,
            json=SAMPLE_FILES_RESPONSE,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)


async def test_get_pr_changed_files(mock_github_api):
    files = await get_pr_changed_files(
        owner="carsten-j",
        repo="pr-review-test-repo",
        pr_number=42,
        github_token="fake-token",
    )
    assert len(files) == 2
    assert files[0].filename == "src/main.py"
    assert files[0].additions == 10
    assert files[0].status == "modified"
    assert files[1].filename == "README.md"


async def test_get_pr_changed_files_api_error(monkeypatch: pytest.MonkeyPatch):
    async def mock_get(self, url, **kwargs):
        return httpx.Response(
            status_code=404,
            json={"message": "Not Found"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with pytest.raises(httpx.HTTPStatusError):
        await get_pr_changed_files(
            owner="carsten-j",
            repo="nonexistent",
            pr_number=1,
            github_token="fake-token",
        )


async def test_get_pr_diff(monkeypatch: pytest.MonkeyPatch):
    async def mock_get(self, url, **kwargs):
        return httpx.Response(
            status_code=200,
            text=SAMPLE_DIFF,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    diff = await get_pr_diff(
        owner="carsten-j",
        repo="pr-review-test-repo",
        pr_number=42,
        github_token="fake-token",
    )
    assert "diff --git" in diff
    assert "+print('hello')" in diff


async def test_get_file_content(monkeypatch: pytest.MonkeyPatch):
    async def mock_get(self, url, **kwargs):
        return httpx.Response(
            status_code=200,
            text="print('hello')\n",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    content = await get_file_content(
        owner="carsten-j",
        repo="pr-review-test-repo",
        path="src/main.py",
        ref="abc123",
        github_token="fake-token",
    )
    assert content == "print('hello')\n"


async def test_search_code(monkeypatch: pytest.MonkeyPatch):
    async def mock_get(self, url, **kwargs):
        return httpx.Response(
            status_code=200,
            json=SAMPLE_SEARCH_RESPONSE,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await search_code(
        owner="carsten-j",
        repo="pr-review-test-repo",
        query="hello",
        github_token="fake-token",
    )
    assert len(results) == 1
    assert results[0].path == "src/main.py"
    assert "def hello():" in results[0].matched_lines
