from __future__ import annotations

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
