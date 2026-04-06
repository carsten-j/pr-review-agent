from __future__ import annotations

import os

import pytest

from pr_review_agent.models import (
    ChangedFile,
    CodeSearchResult,
    PRReview,
    PullRequestInfo,
    RepoInfo,
    TriageResult,
)
from pr_review_agent.review import run_review

REALISTIC_DIFF = """\
diff --git a/src/auth.py b/src/auth.py
index 1a2b3c4..5d6e7f8 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,10 +1,25 @@
+import hashlib
 import os
+from datetime import datetime, timedelta
+
+import jwt

 SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")

-def authenticate(username: str, password: str) -> bool:
-    return username == "admin" and password == "admin"
+def authenticate(username: str, password: str) -> str | None:
+    \"\"\"Authenticate user and return a JWT token, or None on failure.\"\"\"
+    password_hash = hashlib.md5(password.encode()).hexdigest()
+    # TODO: replace with proper DB lookup
+    if username == "admin" and password_hash == "21232f297a57a5a743894a0e4a801fc3":
+        return _create_token(username)
+    return None
+
+def _create_token(username: str) -> str:
+    payload = {
+        "sub": username,
+        "exp": datetime.utcnow() + timedelta(hours=24),
+    }
+    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")
"""

REALISTIC_FILE_CONTENT = """\
import hashlib
import os
from datetime import datetime, timedelta

import jwt

SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")

def authenticate(username: str, password: str) -> str | None:
    \"\"\"Authenticate user and return a JWT token, or None on failure.\"\"\"
    password_hash = hashlib.md5(password.encode()).hexdigest()
    # TODO: replace with proper DB lookup
    if username == "admin" and password_hash == "21232f297a57a5a743894a0e4a801fc3":
        return _create_token(username)
    return None

def _create_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": datetime.utcnow() + timedelta(hours=24),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")
"""


class FakeGitClient:
    async def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        return REALISTIC_DIFF

    async def get_file_content(
        self, workspace: str, repo_slug: str, path: str, ref: str
    ) -> str:
        return REALISTIC_FILE_CONTENT

    async def search_code(
        self, workspace: str, repo_slug: str, query: str
    ) -> list[CodeSearchResult]:
        return []

    async def get_pr_changed_files(
        self, workspace: str, repo_slug: str, pr_id: int
    ) -> list[ChangedFile]:
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
        number=7,
        title="Add JWT authentication",
        body="Replaces the hardcoded admin check with JWT-based auth.",
        author_login="dev-alice",
        html_url="https://github.com/acme/backend/pull/7",
        head_branch="feat/jwt-auth",
        head_sha="aaa111",
        base_branch="main",
    )


@pytest.fixture
def sample_repo() -> RepoInfo:
    return RepoInfo(full_name="acme/backend", is_private=False)


@pytest.fixture
def changed_files() -> list[ChangedFile]:
    return [
        ChangedFile(
            filename="src/auth.py",
            status="modified",
            additions=18,
            deletions=3,
            changes=21,
        ),
    ]


@pytest.fixture
def security_triage() -> TriageResult:
    return TriageResult(
        should_review=True,
        priority="urgent",
        risk_level="high",
        reason="Changes to authentication logic, adds JWT handling",
        tags=["security", "feature"],
    )


@pytest.fixture
def general_triage() -> TriageResult:
    return TriageResult(
        should_review=True,
        priority="normal",
        risk_level="medium",
        reason="Feature addition with moderate complexity",
        tags=["feature"],
    )


@pytest.mark.integration
async def test_security_review_with_real_model(
    sample_pr: PullRequestInfo,
    sample_repo: RepoInfo,
    changed_files: list[ChangedFile],
    security_triage: TriageResult,
):
    """Integration test: security review agent with real Anthropic API."""
    assert os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY must be set"

    review = await run_review(
        pr=sample_pr,
        repo=sample_repo,
        git_client=FakeGitClient(),
        triage_result=security_triage,
        changed_files=changed_files,
    )

    assert isinstance(review, PRReview)
    assert review.summary
    assert review.risk_level in ("low", "medium", "high", "critical")
    assert isinstance(review.comments, list)
    assert isinstance(review.approve, bool)

    # The diff has obvious security issues (md5, weak default secret);
    # the security agent should flag at least one comment.
    assert len(review.comments) > 0
    for comment in review.comments:
        assert comment.file_path
        assert comment.line_start > 0
        assert comment.severity in ("info", "warning", "error", "critical")
        assert comment.category
        assert comment.comment


@pytest.mark.integration
async def test_general_review_with_real_model(
    sample_pr: PullRequestInfo,
    sample_repo: RepoInfo,
    changed_files: list[ChangedFile],
    general_triage: TriageResult,
):
    """Integration test: general review agent with real Anthropic API."""
    assert os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY must be set"

    review = await run_review(
        pr=sample_pr,
        repo=sample_repo,
        git_client=FakeGitClient(),
        triage_result=general_triage,
        changed_files=changed_files,
    )

    assert isinstance(review, PRReview)
    assert review.summary
    assert review.risk_level in ("low", "medium", "high", "critical")
    assert isinstance(review.comments, list)
    assert isinstance(review.approve, bool)
