from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, cast

import logfire
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam, ToolUseBlock

from pr_review_agent.git_platform import GitPlatformClient, RepoDeps
from pr_review_agent.models import (
    ChangedFile,
    CodeSearchResult,
    GitHubPullRequest,
    GitHubRepo,
    PRReview,
    TriageResult,
)

logger = logging.getLogger(__name__)


def _slice_file_content(
    content: str, line_start: int | None, line_end: int | None
) -> str:
    """Return content optionally sliced to [line_start, line_end] (1-indexed, inclusive)."""
    if line_start is None and line_end is None:
        return content
    lines = content.splitlines(keepends=True)
    total = len(lines)
    s = (line_start or 1) - 1  # convert to 0-indexed
    e = line_end or total
    sliced = "".join(lines[s:e])
    header = f"# Lines {line_start or 1}-{min(e, total)} of {total}\n"
    return header + sliced


@dataclass
class ReviewDeps(RepoDeps):
    pr: GitHubPullRequest
    repo: GitHubRepo
    changed_files: list[ChangedFile]
    triage: TriageResult
    reviewer_role: str = field(default="senior-dev")


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

GENERAL_REVIEW_SYSTEM_PROMPT = """\
You provide thorough, constructive code reviews focused on quality, \
correctness, and maintainability.

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

_FETCH_PR_DIFF_TOOL: ToolParam = cast(
    "ToolParam",
    {
        "name": "fetch_pr_diff",
        "description": "Fetch the full unified diff of the pull request.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
)

_FETCH_FILE_CONTENT_TOOL: ToolParam = cast(
    "ToolParam",
    {
        "name": "fetch_file_content",
        "description": (
            "Fetch the content of a file at the PR's head ref. "
            "Prefer targeted line ranges when reviewing diff hunks to stay within "
            "context limits — e.g. line_start=10, line_end=60 for a 50-line hunk. "
            "Omit line_start/line_end only when you need the whole file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Repo-relative file path",
                },
                "line_start": {
                    "type": "integer",
                    "description": "First line to return (1-indexed, inclusive). Omit for start of file.",
                },
                "line_end": {
                    "type": "integer",
                    "description": "Last line to return (1-indexed, inclusive). Omit for end of file.",
                },
            },
            "required": ["file_path"],
        },
    },
)

_SEARCH_REPO_CODE_TOOL: ToolParam = cast(
    "ToolParam",
    {
        "name": "search_repo_code",
        "description": (
            "Search the repository for code matching a query. "
            "Use this to find where functions are defined, how interfaces are "
            "implemented, or to check for similar patterns elsewhere."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
            },
            "required": ["query"],
        },
    },
)

_SUBMIT_REVIEW_TOOL: ToolParam = cast(
    "ToolParam",
    {
        "name": "submit_review",
        "description": "Submit the completed structured pull request review.",
        "input_schema": PRReview.model_json_schema(),
    },
)

_ALL_REVIEW_TOOLS: list[ToolParam] = [
    _FETCH_PR_DIFF_TOOL,
    _FETCH_FILE_CONTENT_TOOL,
    _SEARCH_REPO_CODE_TOOL,
    _SUBMIT_REVIEW_TOOL,
]


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
        f"When fetching file content, prefer line ranges (line_start/line_end) "
        f"targeting the relevant diff hunk to stay within context limits."
    )


async def _run_review_loop(
    system: str,
    user_prompt: str,
    tool_executors: dict[str, Any],
    anthropic_client: AsyncAnthropic,
    max_iterations: int = 20,
) -> PRReview:
    """Drive the tool-use agentic loop until submit_review is called."""
    messages: list[Any] = [{"role": "user", "content": user_prompt}]

    for iteration in range(max_iterations):
        response = await anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=16000,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            tools=_ALL_REVIEW_TOOLS,
            messages=cast("list[MessageParam]", messages),
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Review response was truncated at iteration {iteration} "
                "— increase max_tokens"
            )
        if response.stop_reason == "end_turn":
            raise RuntimeError(
                f"Review agent stopped without submitting a review "
                f"(iteration {iteration})"
            )

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_block = cast(ToolUseBlock, block)
            if tool_block.name == "submit_review":
                return PRReview.model_validate(tool_block.input)
            with logfire.span(
                "tool: {tool}", tool=tool_block.name, input=tool_block.input
            ):
                result = await tool_executors[tool_block.name](tool_block.input)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": str(result),
                }
            )

        if not tool_results:
            raise RuntimeError(
                f"No tool calls in response at iteration {iteration}, "
                f"stop_reason={response.stop_reason}"
            )

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Review agent exceeded max_iterations={max_iterations}")


async def run_review(
    pr: GitHubPullRequest,
    repo: GitHubRepo,
    git_client: GitPlatformClient,
    triage_result: TriageResult,
    changed_files: list[ChangedFile],
    anthropic_client: AsyncAnthropic,
    reviewer_role: str = "senior-dev",
) -> PRReview:
    """Run the appropriate review agent based on triage tags."""
    workspace, repo_slug = repo.full_name.split("/", 1)

    _file_cache: dict[tuple[str, int | None, int | None], str] = {}

    async def _fetch_pr_diff(_input: dict[str, Any]) -> str:
        return await git_client.get_pr_diff(workspace, repo_slug, pr.number)

    async def _fetch_file_content(input_: dict[str, Any]) -> str:
        file_path: str = input_["file_path"]
        line_start: int | None = input_.get("line_start")
        line_end: int | None = input_.get("line_end")
        cache_key = (file_path, line_start, line_end)
        if cache_key not in _file_cache:
            raw = await git_client.get_file_content(
                workspace, repo_slug, file_path, pr.head.sha
            )
            _file_cache[cache_key] = _slice_file_content(raw, line_start, line_end)
        return _file_cache[cache_key]

    async def _search_repo_code(input_: dict[str, Any]) -> str:
        results: list[CodeSearchResult] = await git_client.search_code(
            workspace, repo_slug, input_["query"]
        )
        return json.dumps([r.model_dump() for r in results])

    tool_executors: dict[str, Any] = {
        "fetch_pr_diff": _fetch_pr_diff,
        "fetch_file_content": _fetch_file_content,
        "search_repo_code": _search_repo_code,
    }

    is_security = "security" in triage_result.tags
    if is_security:
        system = SECURITY_REVIEW_SYSTEM_PROMPT
    else:
        system = f"You are a {reviewer_role} reviewing a pull request.\n\n{GENERAL_REVIEW_SYSTEM_PROMPT}"

    logger.info(
        "Running %s review for PR #%d",
        "security" if is_security else "general",
        pr.number,
    )

    deps = ReviewDeps(
        git_client=git_client,
        workspace=workspace,
        repo_slug=repo_slug,
        pr_id=pr.number,
        pr=pr,
        repo=repo,
        changed_files=changed_files,
        triage=triage_result,
        reviewer_role=reviewer_role,
    )

    return await _run_review_loop(
        system=system,
        user_prompt=_format_review_prompt(deps),
        tool_executors=tool_executors,
        anthropic_client=anthropic_client,
    )
