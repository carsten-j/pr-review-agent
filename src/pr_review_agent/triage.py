from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam, ToolUseBlock

from pr_review_agent.git_platform import GitPlatformClient, RepoDeps
from pr_review_agent.models import (
    ChangedFile,
    GitHubPullRequest,
    GitHubRepo,
    TriageResult,
)

logger = logging.getLogger(__name__)


@dataclass
class TriageDeps(RepoDeps):
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

_TRIAGE_OUTPUT_TOOL: ToolParam = cast(
    "ToolParam",
    {
        "name": "produce_triage_result",
        "description": "Produce the structured triage assessment for the pull request.",
        "input_schema": TriageResult.model_json_schema(),
    },
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
    git_client: GitPlatformClient,
    anthropic_client: AsyncAnthropic,
) -> TriageResult:
    """Fetch changed files and run the triage agent."""
    workspace, repo_slug = repo.full_name.split("/", 1)
    changed_files = await git_client.get_pr_changed_files(
        workspace=workspace,
        repo_slug=repo_slug,
        pr_id=pr.number,
    )

    deps = TriageDeps(
        git_client=git_client,
        workspace=workspace,
        repo_slug=repo_slug,
        pr_id=pr.number,
        pr=pr,
        repo=repo,
        changed_files=changed_files,
    )

    response = await anthropic_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": TRIAGE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[_TRIAGE_OUTPUT_TOOL],
        tool_choice={"type": "tool", "name": "produce_triage_result"},
        messages=cast(
            "list[MessageParam]",
            [{"role": "user", "content": _format_user_prompt(deps)}],
        ),
    )

    tool_use_block = cast(
        ToolUseBlock,
        next(b for b in response.content if b.type == "tool_use"),
    )
    return TriageResult.model_validate(tool_use_block.input)
