from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

from pr_review_agent import github_client
from pr_review_agent.models import (
    ChangedFile,
    CodeSearchResult,
    GitHubPullRequest,
    GitHubRepo,
    PRReview,
    TriageResult,
)

logger = logging.getLogger(__name__)

REVIEWER_ROLE = os.environ.get("REVIEWER_ROLE", "senior-dev")


@dataclass
class ReviewDeps:
    github_token: str
    pr: GitHubPullRequest
    repo: GitHubRepo
    changed_files: list[ChangedFile]
    triage: TriageResult


SECURITY_REVIEW_SYSTEM_PROMPT = """\
You are a security specialist reviewing a pull request. Your focus is on \
identifying vulnerabilities, insecure patterns, and security risks.

Focus areas:
- Injection vulnerabilities (SQL, command, XSS, LDAP, etc.)
- Authentication and authorization flaws
- Secrets or credentials in code (hardcoded keys, tokens, passwords)
- Insecure cryptographic practices
- Input validation and sanitization gaps
- Insecure deserialization
- Missing access controls
- Information leakage (verbose errors, debug endpoints, logs with PII)
- Dependency vulnerabilities

For each issue found:
- Reference the exact file path and line number(s) from the diff
- Classify severity: info, warning, error, or critical
- Provide a concrete suggestion for how to fix it
- Explain the potential attack vector

Also note architectural security observations and provide learning points \
for junior developers about secure coding practices.

Use the available tools to fetch the PR diff, read file contents for context, \
and search the codebase for related patterns.\
"""

GENERAL_REVIEW_SYSTEM_PROMPT = f"""\
You are a {REVIEWER_ROLE} reviewing a pull request. You provide thorough, \
constructive code reviews focused on quality, correctness, and maintainability.

Focus areas:
- Correctness and edge cases
- Error handling and resilience
- Naming, readability, and code clarity
- Performance implications
- Test coverage gaps
- API design and contracts
- Code duplication and abstraction opportunities
- Consistency with existing patterns

For each issue found:
- Reference the exact file path and line number(s) from the diff
- Classify severity: info, warning, error, or critical
- Categorize the issue (e.g., "naming", "error-handling", "performance")
- Provide a concrete suggestion when possible

Also note architectural observations (coupling, missing patterns, boundary \
violations) and provide learning points for junior developers.

Use the available tools to fetch the PR diff, read file contents for context, \
and search the codebase for related patterns.\
"""


security_review_agent = Agent(
    "anthropic:claude-sonnet-4-5",
    deps_type=ReviewDeps,
    output_type=PRReview,
    system_prompt=SECURITY_REVIEW_SYSTEM_PROMPT,
)

general_review_agent = Agent(
    "anthropic:claude-sonnet-4-5",
    deps_type=ReviewDeps,
    output_type=PRReview,
    system_prompt=GENERAL_REVIEW_SYSTEM_PROMPT,
)


def _register_tools(agent: Agent[ReviewDeps, PRReview]) -> None:
    """Register review tools on an agent."""

    @agent.tool
    async def fetch_pr_diff(ctx: RunContext[ReviewDeps]) -> str:
        """Fetch the full unified diff of the pull request."""
        owner, repo_name = ctx.deps.repo.full_name.split("/", 1)
        return await github_client.get_pr_diff(
            owner, repo_name, ctx.deps.pr.number, ctx.deps.github_token
        )

    @agent.tool
    async def fetch_file_content(ctx: RunContext[ReviewDeps], file_path: str) -> str:
        """Fetch the full content of a file at the PR's head ref.
        Use this to see surrounding context beyond what's in the diff."""
        owner, repo_name = ctx.deps.repo.full_name.split("/", 1)
        return await github_client.get_file_content(
            owner, repo_name, file_path, ctx.deps.pr.head.sha, ctx.deps.github_token
        )

    @agent.tool
    async def search_repo_code(
        ctx: RunContext[ReviewDeps], query: str
    ) -> list[CodeSearchResult]:
        """Search the repository for code matching a query.
        Use this to find where functions are defined, how interfaces are
        implemented, or to check for similar patterns elsewhere."""
        owner, repo_name = ctx.deps.repo.full_name.split("/", 1)
        return await github_client.search_code(
            owner, repo_name, query, ctx.deps.github_token
        )


_register_tools(security_review_agent)
_register_tools(general_review_agent)


def _format_review_prompt(deps: ReviewDeps) -> str:
    """Build the user message from PR metadata, changed files, and triage context."""
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
        f"Triage assessment: {deps.triage.risk_level} risk, "
        f"priority={deps.triage.priority}, tags={deps.triage.tags}\n"
        f"Triage reason: {deps.triage.reason}\n\n"
        f"Changed files ({len(deps.changed_files)}):\n{files_summary}\n\n"
        f"Start by fetching the PR diff, then review each changed file. "
        f"Fetch full file content when you need more context."
    )


async def run_review(
    pr: GitHubPullRequest,
    repo: GitHubRepo,
    github_token: str,
    triage_result: TriageResult,
    changed_files: list[ChangedFile],
) -> PRReview:
    """Run the appropriate review agent based on triage tags."""
    deps = ReviewDeps(
        github_token=github_token,
        pr=pr,
        repo=repo,
        changed_files=changed_files,
        triage=triage_result,
    )

    agent = (
        security_review_agent
        if "security" in triage_result.tags
        else general_review_agent
    )

    logger.info(
        "Running %s review for PR #%d",
        "security" if "security" in triage_result.tags else "general",
        pr.number,
    )

    result = await agent.run(
        _format_review_prompt(deps),
        deps=deps,
    )
    return result.output  # ty: ignore[invalid-return-type]  # Pydantic AI generic
