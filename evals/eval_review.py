"""Review agent evals: does the review agent find real issues and avoid false positives?"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic_ai import Agent
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from pr_review_agent.models import (
    ChangedFile,
    CodeSearchResult,
    GitHubBranchRef,
    GitHubPullRequest,
    GitHubRepo,
    GitHubUser,
    PRReview,
    TriageResult,
)
from pr_review_agent.review import run_review

# ---------------------------------------------------------------------------
# Fake git client
# ---------------------------------------------------------------------------


class FakeGitClient:
    def __init__(self, diff: str = "", file_content: str = "") -> None:
        self._diff = diff
        self._file_content = file_content

    async def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        return self._diff

    async def get_file_content(
        self, workspace: str, repo_slug: str, path: str, ref: str
    ) -> str:
        return self._file_content

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


# ---------------------------------------------------------------------------
# Input / output types
# ---------------------------------------------------------------------------


@dataclass
class ReviewInputs:
    pr: GitHubPullRequest
    repo: GitHubRepo
    triage: TriageResult
    diff: str
    file_content: str
    changed_files: list[ChangedFile]
    expect_security_issue: bool = False
    expect_approval: bool = False


def _make_pr(number: int, title: str, body: str = "") -> GitHubPullRequest:
    return GitHubPullRequest(
        number=number,
        title=title,
        body=body,
        state="open",
        user=GitHubUser(login="testuser", id=1),
        html_url=f"https://github.com/carsten-j/test-repo/pull/{number}",
        diff_url=f"https://github.com/carsten-j/test-repo/pull/{number}.diff",
        head=GitHubBranchRef(ref="feature", sha="abc123"),
        base=GitHubBranchRef(ref="main", sha="def456"),
        created_at="2026-04-05T10:00:00Z",
        updated_at="2026-04-05T10:00:00Z",
    )


SAMPLE_REPO = GitHubRepo(
    full_name="carsten-j/test-repo",
    clone_url="https://github.com/carsten-j/test-repo.git",
    private=False,
)


def _file(filename: str, additions: int = 20, deletions: int = 2) -> ChangedFile:
    return ChangedFile(
        filename=filename,
        status="modified",
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
    )


def _triage(
    priority: str = "normal", risk: str = "medium", tags: list[str] | None = None
) -> TriageResult:
    return TriageResult(
        should_review=True,
        priority=priority,  # type: ignore[arg-type]
        risk_level=risk,  # type: ignore[arg-type]
        reason="Triage assessment",
        tags=tags or [],
    )


# ---------------------------------------------------------------------------
# Task function
# ---------------------------------------------------------------------------


async def review_task(inputs: ReviewInputs) -> PRReview:
    fake_client = FakeGitClient(diff=inputs.diff, file_content=inputs.file_content)
    return await run_review(
        pr=inputs.pr,
        repo=inputs.repo,
        git_client=fake_client,
        triage_result=inputs.triage,
        changed_files=inputs.changed_files,
        reviewer_role="senior-dev",
    )


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


@dataclass
class FindsSecurityIssue(Evaluator[ReviewInputs, PRReview]):
    """For security cases: must have comments with error/critical severity."""

    def evaluate(self, ctx: EvaluatorContext[ReviewInputs, PRReview, None]) -> float:
        if not ctx.inputs.expect_security_issue:
            return 1.0
        has_comments = len(ctx.output.comments) > 0
        has_critical = any(
            c.severity in ("error", "critical") for c in ctx.output.comments
        )
        return 1.0 if (has_comments and has_critical) else 0.0


@dataclass
class ApprovesCleanCode(Evaluator[ReviewInputs, PRReview]):
    """For clean code cases: must approve."""

    def evaluate(self, ctx: EvaluatorContext[ReviewInputs, PRReview, None]) -> float:
        if not ctx.inputs.expect_approval:
            return 1.0
        return 1.0 if ctx.output.approve else 0.0


_judge_agent: Agent[None, float] = Agent(
    "anthropic:claude-haiku-4-5",
    output_type=float,
    instructions=(
        "Score 0.0–1.0: are these code review comments actionable and specific to "
        "the actual code change described? Reply with only a number between 0.0 and 1.0."
    ),
)


@dataclass
class CommentQuality(Evaluator[ReviewInputs, PRReview]):
    """LLM judge: are comments actionable and specific to the code change?"""

    async def evaluate(
        self, ctx: EvaluatorContext[ReviewInputs, PRReview, None]
    ) -> float:
        if not ctx.output.comments:
            # No comments — can't score quality; return neutral
            return 0.5
        comments_text = "\n".join(
            f"- [{c.severity}] {c.comment}" for c in ctx.output.comments[:5]
        )
        result = await _judge_agent.run(
            f"PR diff snippet:\n{ctx.inputs.diff[:500]}\n\nComments:\n{comments_text}"
        )
        score = result.output  # ty: ignore[invalid-return-type]
        return max(0.0, min(1.0, float(score)))


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

_MD5_DIFF = """\
--- a/auth.py
+++ b/auth.py
@@ -10,7 +10,7 @@
 import hashlib

 def hash_password(password: str) -> str:
-    return hashlib.sha256(password.encode()).hexdigest()
+    return hashlib.md5(password.encode()).hexdigest()
"""

_MD5_FILE = """\
import hashlib

def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()

def verify_password(password: str, stored_hash: str) -> bool:
    return hash_password(password) == stored_hash
"""

_SQL_DIFF = """\
--- a/db.py
+++ b/db.py
@@ -5,6 +5,8 @@
 import sqlite3

+def get_user(username: str) -> dict:
+    conn = sqlite3.connect("app.db")
+    cursor = conn.execute(f"SELECT * FROM users WHERE name = '{username}'")
+    return cursor.fetchone()
"""

_SQL_FILE = """\
import sqlite3

def get_user(username: str) -> dict:
    conn = sqlite3.connect("app.db")
    cursor = conn.execute(f"SELECT * FROM users WHERE name = '{username}'")
    return cursor.fetchone()
"""

_CLEAN_DIFF = """\
--- a/utils.py
+++ b/utils.py
@@ -1,5 +1,5 @@
-def calc_total(x, y):
-    return x + y
+def calculate_total(x: int, y: int) -> int:
+    return x + y
"""

_CLEAN_FILE = """\
def calculate_total(x: int, y: int) -> int:
    return x + y
"""

_NO_ERROR_HANDLING_DIFF = """\
--- a/client.py
+++ b/client.py
@@ -1,4 +1,8 @@
 import requests

+def fetch_data(url: str) -> dict:
+    response = requests.get(url)
+    return response.json()
"""

_NO_ERROR_HANDLING_FILE = """\
import requests

def fetch_data(url: str) -> dict:
    response = requests.get(url)
    return response.json()
"""

review_dataset: Dataset[ReviewInputs, PRReview] = Dataset(
    name="review-evals",
    cases=[
        Case(
            name="md5_password_hash",
            inputs=ReviewInputs(
                pr=_make_pr(1, "Switch to MD5 hashing", "Simplify password hashing"),
                repo=SAMPLE_REPO,
                triage=_triage(priority="urgent", risk="high", tags=["security"]),
                diff=_MD5_DIFF,
                file_content=_MD5_FILE,
                changed_files=[_file("auth.py")],
                expect_security_issue=True,
            ),
        ),
        Case(
            name="sql_injection",
            inputs=ReviewInputs(
                pr=_make_pr(2, "Add user lookup", "Add get_user by username"),
                repo=SAMPLE_REPO,
                triage=_triage(priority="urgent", risk="high", tags=["security"]),
                diff=_SQL_DIFF,
                file_content=_SQL_FILE,
                changed_files=[_file("db.py")],
                expect_security_issue=True,
            ),
        ),
        Case(
            name="clean_refactor",
            inputs=ReviewInputs(
                pr=_make_pr(3, "Rename and type calc function"),
                repo=SAMPLE_REPO,
                triage=_triage(priority="normal", risk="low"),
                diff=_CLEAN_DIFF,
                file_content=_CLEAN_FILE,
                changed_files=[_file("utils.py", additions=2, deletions=2)],
                expect_approval=True,
            ),
        ),
        Case(
            name="missing_error_handling",
            inputs=ReviewInputs(
                pr=_make_pr(4, "Add data fetch helper"),
                repo=SAMPLE_REPO,
                triage=_triage(priority="normal", risk="medium"),
                diff=_NO_ERROR_HANDLING_DIFF,
                file_content=_NO_ERROR_HANDLING_FILE,
                changed_files=[_file("client.py")],
            ),
        ),
    ],
    evaluators=[
        FindsSecurityIssue(),
        ApprovesCleanCode(),
        CommentQuality(),
    ],
)


# ---------------------------------------------------------------------------
# pytest integration
# ---------------------------------------------------------------------------


@pytest.mark.evals
async def test_review_evals() -> None:
    report = await review_dataset.evaluate(review_task)
    report.print(include_input=True, include_output=True, include_durations=True)
    # Assert no complete failures on deterministic evaluators
    for row in report.rows:
        scores = {s.evaluator_name: s.value for s in row.scores}
        assert scores.get("FindsSecurityIssue", 1.0) > 0.0, (
            f"Case {row.case_name!r} failed to find a security issue"
        )
        assert scores.get("ApprovesCleanCode", 1.0) > 0.0, (
            f"Case {row.case_name!r} failed to approve clean code"
        )
