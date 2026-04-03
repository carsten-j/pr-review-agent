from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic_ai import Agent

from pr_review_agent.github_client import get_pr_changed_files
from pr_review_agent.models import (
    ChangedFile,
    GitHubPullRequest,
    GitHubRepo,
    TriageResult,
)

logger = logging.getLogger(__name__)


@dataclass
class TriageDeps:
    github_token: str
    pr: GitHubPullRequest
    repo: GitHubRepo
    changed_files: list[ChangedFile]


TRIAGE_SYSTEM_PROMPT = """\
You are a pull request triage agent. Given metadata about a GitHub pull request \
and the list of changed files, produce a triage assessment.

Evaluate the following:

1. **should_review** - Should a human review this PR? Say false only for trivial \
changes like typo fixes in comments, minor formatting, or dependency bumps with \
no code changes.

2. **priority** - "urgent" if the PR touches security-sensitive code (auth, crypto, \
permissions, secrets), production infrastructure (CI/CD, Dockerfiles, deploy configs), \
or database migrations. Otherwise "normal".

3. **risk_level**:
   - "low": documentation, tests, minor refactors, config tweaks
   - "medium": feature additions, moderate refactors, dependency updates
   - "high": changes to core business logic, API contracts, data models, auth
   - "critical": security fixes, breaking changes, large-scale refactors (50+ files)

4. **reason** - A concise 1-2 sentence explanation of your assessment.

5. **tags** - Relevant labels from: docs, tests, feature, bugfix, refactor, \
dependencies, security, infrastructure, breaking-change, database, api, config, ci.\
"""

triage_agent = Agent(
    "anthropic:claude-haiku-4-5",
    deps_type=TriageDeps,
    output_type=TriageResult,
    system_prompt=TRIAGE_SYSTEM_PROMPT,
)


def _format_user_prompt(deps: TriageDeps) -> str:
    """Build the user message from PR metadata and changed files."""
    files_summary = "\n".join(
        f"  - {f.filename} ({f.status}): +{f.additions} -{f.deletions}"
        for f in deps.changed_files
    )
    return (
        f"PR #{deps.pr.number}: {deps.pr.title}\n"
        f"Author: {deps.pr.user.login}\n"
        f"Repository: {deps.repo.full_name}\n"
        f"Branch: {deps.pr.head.ref} -> {deps.pr.base.ref}\n"
        f"Description: {deps.pr.body or '(no description)'}\n\n"
        f"Changed files ({len(deps.changed_files)}):\n{files_summary}"
    )


async def run_triage(
    pr: GitHubPullRequest,
    repo: GitHubRepo,
    github_token: str,
) -> TriageResult:
    """Fetch changed files and run the triage agent."""
    owner, repo_name = repo.full_name.split("/", 1)
    changed_files = await get_pr_changed_files(
        owner=owner,
        repo=repo_name,
        pr_number=pr.number,
        github_token=github_token,
    )

    deps = TriageDeps(
        github_token=github_token,
        pr=pr,
        repo=repo,
        changed_files=changed_files,
    )

    result = await triage_agent.run(
        _format_user_prompt(deps),
        deps=deps,
    )
    return result.output  # ty: ignore[invalid-return-type]  # Pydantic AI generic
