"""Unit tests for BitbucketClient — mirrors test_git_client.py structure."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pr_review_agent.bitbucket_client import BitbucketClient
from pr_review_agent.models import ChangedFile, CodeSearchResult


def _make_client() -> BitbucketClient:
    return BitbucketClient("testuser", "testpassword")


def _mock_response(
    json_data: dict | list | None = None, text: str = "", status: int = 200
) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status
    mock.text = text
    mock.json.return_value = json_data or {}
    if status >= 400:
        mock.raise_for_status.side_effect = Exception(f"HTTP {status}")
    else:
        mock.raise_for_status.return_value = None
    return mock


@pytest.fixture
def client() -> BitbucketClient:
    return _make_client()


async def test_get_pr_changed_files(client: BitbucketClient):
    """Maps Bitbucket diffstat response to ChangedFile objects."""
    diffstat = {
        "values": [
            {
                "status": "modified",
                "new": {"path": "src/main.py", "type": "commit_file"},
                "old": {"path": "src/main.py", "type": "commit_file"},
                "lines_added": 10,
                "lines_removed": 3,
            },
            {
                "status": "added",
                "new": {"path": "src/new_file.py", "type": "commit_file"},
                "lines_added": 20,
                "lines_removed": 0,
            },
        ]
    }
    with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(json_data=diffstat)
        result = await client.get_pr_changed_files("owner", "repo", 42)

    assert len(result) == 2
    assert isinstance(result[0], ChangedFile)
    assert result[0].filename == "src/main.py"
    assert result[0].status == "modified"
    assert result[0].additions == 10
    assert result[0].deletions == 3
    assert result[0].changes == 13
    assert result[1].filename == "src/new_file.py"
    assert result[1].additions == 20
    mock_get.assert_called_once_with(
        "https://api.bitbucket.org/2.0/repositories/owner/repo/pullrequests/42/diffstat"
    )


async def test_get_pr_changed_files_api_error(client: BitbucketClient):
    """Raises on non-2xx response."""
    with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(status=403)
        with pytest.raises(Exception):
            await client.get_pr_changed_files("owner", "repo", 1)


async def test_get_pr_diff(client: BitbucketClient):
    """Returns raw diff text from Bitbucket."""
    diff_text = "diff --git a/foo.py b/foo.py\n+print('hello')\n"
    with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(text=diff_text)
        result = await client.get_pr_diff("owner", "repo", 7)

    assert result == diff_text
    mock_get.assert_called_once_with(
        "https://api.bitbucket.org/2.0/repositories/owner/repo/pullrequests/7/diff"
    )


async def test_get_file_content(client: BitbucketClient):
    """Returns raw file content from Bitbucket src endpoint."""
    content = "def hello():\n    print('hi')\n"
    with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(text=content)
        result = await client.get_file_content(
            "owner", "repo", "src/hello.py", "abc123"
        )

    assert result == content
    mock_get.assert_called_once_with(
        "https://api.bitbucket.org/2.0/repositories/owner/repo/src/abc123/src/hello.py"
    )


async def test_search_code(client: BitbucketClient):
    """Maps Bitbucket search response to CodeSearchResult objects."""
    search_response = {
        "values": [
            {
                "file": {"path": "src/auth.py", "type": "commit_file"},
                "content_matches": [
                    {
                        "lines": [
                            {"line": "def authenticate(", "line_type": "context"},
                            {"line": "    return True", "line_type": "match"},
                        ]
                    }
                ],
            }
        ]
    }
    with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(json_data=search_response)
        result = await client.search_code("owner", "repo", "authenticate")

    assert len(result) == 1
    assert isinstance(result[0], CodeSearchResult)
    assert result[0].path == "src/auth.py"
    assert "def authenticate(" in result[0].matched_lines
    mock_get.assert_called_once_with(
        "https://api.bitbucket.org/2.0/repositories/owner/repo/search/code",
        params={"search_query": "authenticate"},
    )


async def test_search_code_unavailable_returns_empty(client: BitbucketClient):
    """Returns empty list and does not raise when code search is unavailable (403/404)."""
    for status in (403, 404):
        with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _mock_response(status=status)
            result = await client.search_code("owner", "repo", "anything")
        assert result == [], f"Expected empty list for status {status}"


async def test_post_review_approve(client: BitbucketClient):
    """Posts summary comment, inline comments, and approves the PR."""
    with patch.object(client._http, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response()
        await client.post_review(
            "owner",
            "repo",
            42,
            body="Looks good!",
            event="APPROVE",
            comments=[
                {
                    "path": "src/main.py",
                    "line": 10,
                    "body": "[info] naming: rename this",
                },
            ],
        )

    # summary comment + 1 inline comment + approve = 3 calls
    assert mock_post.call_count == 3
    calls = mock_post.call_args_list

    # First call: summary comment
    assert calls[0].args[0].endswith("/comments")
    assert calls[0].kwargs["json"]["content"]["raw"] == "Looks good!"

    # Second call: inline comment
    assert calls[1].args[0].endswith("/comments")
    assert calls[1].kwargs["json"]["inline"]["path"] == "src/main.py"
    assert calls[1].kwargs["json"]["inline"]["to"] == 10

    # Third call: approve
    assert calls[2].args[0].endswith("/approve")


async def test_post_review_request_changes(client: BitbucketClient):
    """Posts summary comment, inline comments, and requests changes."""
    with patch.object(client._http, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response()
        await client.post_review(
            "owner",
            "repo",
            5,
            body="Needs work",
            event="REQUEST_CHANGES",
            comments=[],
        )

    # summary comment + request-changes = 2 calls (no inline comments)
    assert mock_post.call_count == 2
    last_call_url = mock_post.call_args_list[-1].args[0]
    assert last_call_url.endswith("/request-changes")


async def test_post_review_raises_on_error(client: BitbucketClient):
    """Raises when the API returns a non-2xx status."""
    with patch.object(client._http, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(status=403)
        with pytest.raises(Exception):
            await client.post_review("owner", "repo", 1, "body", "APPROVE", [])
