from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class GitHubUser(BaseModel):
    login: str
    id: int
    avatar_url: str | None = None


class GitHubBranchRef(BaseModel):
    ref: str
    sha: str


class GitHubRepo(BaseModel):
    full_name: str
    clone_url: str
    private: bool


class GitHubPullRequest(BaseModel):
    number: int
    title: str
    body: str | None = None
    state: str
    user: GitHubUser
    html_url: str
    diff_url: str
    head: GitHubBranchRef
    base: GitHubBranchRef
    created_at: str
    updated_at: str


class GitHubWebhookPayload(BaseModel):
    action: str
    pull_request: GitHubPullRequest = Field(alias="pull_request")
    repository: GitHubRepo
    sender: GitHubUser


# ---------------------------------------------------------------------------
# Bitbucket webhook models
# ---------------------------------------------------------------------------


class BitbucketActor(BaseModel):
    nickname: str
    display_name: str


class BitbucketBranch(BaseModel):
    name: str


class BitbucketCommit(BaseModel):
    hash: str


class BitbucketEndpoint(BaseModel):
    branch: BitbucketBranch
    commit: BitbucketCommit


class BitbucketLinks(BaseModel):
    html: dict  # {"href": "https://..."}


class BitbucketPullRequest(BaseModel):
    id: int
    title: str
    description: str | None = None
    state: str
    source: BitbucketEndpoint
    destination: BitbucketEndpoint
    links: BitbucketLinks


class BitbucketRepo(BaseModel):
    full_name: str
    is_private: bool


class BitbucketWebhookPayload(BaseModel):
    actor: BitbucketActor
    pullrequest: BitbucketPullRequest
    repository: BitbucketRepo


@dataclass
class PullRequestInfo:
    """Platform-agnostic pull request domain object."""

    number: int
    title: str
    body: str | None
    author_login: str
    html_url: str
    head_branch: str
    head_sha: str
    base_branch: str


@dataclass
class RepoInfo:
    """Platform-agnostic repository domain object."""

    full_name: str
    is_private: bool


class ChangedFile(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int


class TriageResult(BaseModel):
    should_review: bool
    priority: Literal["normal", "urgent"]
    risk_level: Literal["low", "medium", "high", "critical"]
    reason: str
    tags: list[str]


class CodeSearchResult(BaseModel):
    path: str
    matched_lines: list[str]


class ReviewComment(BaseModel):
    file_path: str
    line_start: int
    line_end: int | None = None
    severity: Literal["info", "warning", "error", "critical"]
    category: str = Field(
        description="e.g. 'security', 'naming', 'error-handling', 'performance', 'test-coverage'"
    )
    comment: str
    suggestion: str | None = Field(
        default=None, description="Concrete code suggestion if applicable"
    )


class ArchitecturalObservation(BaseModel):
    pattern: str = Field(description="e.g. 'missing-error-handling', 'tight-coupling'")
    description: str
    affected_files: list[str]


class PRReview(BaseModel):
    summary: str = Field(
        description="2-3 sentence summary of the PR's intent and quality"
    )
    risk_level: Literal["low", "medium", "high", "critical"]
    comments: list[ReviewComment]
    architectural_observations: list[ArchitecturalObservation] = Field(
        default_factory=list
    )
    learning_points: list[str] = Field(
        default_factory=list,
        description="Key takeaways for junior developers",
    )
    approve: bool = Field(description="Whether the PR is safe to merge as-is")
