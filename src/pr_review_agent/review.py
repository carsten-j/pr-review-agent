from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, cast

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
    ReviewComment,
    TriageResult,
)

logger = logging.getLogger(__name__)

_PARALLEL_THRESHOLD = 3  # fan out when PR touches this many files or more
_FILE_REVIEW_CONCURRENCY = 4  # max simultaneous file-review subagents
_FILE_REVIEW_MAX_ITERATIONS = 5  # per-file loop guard

_RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


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


def _split_diff_by_file(diff: str) -> dict[str, str]:
    """Split a unified diff string into per-file snippets keyed by the b/ path."""
    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    result: dict[str, str] = {}
    for section in sections:
        m = re.match(r"diff --git a/\S+ b/(\S+)", section)
        if m:
            result[m.group(1)] = section
    return result


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

_SUBMIT_FILE_REVIEW_TOOL: ToolParam = cast(
    "ToolParam",
    {
        "name": "submit_file_review",
        "description": "Submit review comments for this individual file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "comments": {
                    "type": "array",
                    "items": ReviewComment.model_json_schema(),
                    "description": "Review comments for this file",
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Risk level of this file's changes",
                },
            },
            "required": ["comments", "risk_level"],
        },
    },
)

_ALL_REVIEW_TOOLS: list[ToolParam] = [
    _FETCH_PR_DIFF_TOOL,
    _FETCH_FILE_CONTENT_TOOL,
    _SEARCH_REPO_CODE_TOOL,
    _SUBMIT_REVIEW_TOOL,
]

_FILE_REVIEW_TOOLS: list[ToolParam] = [
    _FETCH_FILE_CONTENT_TOOL,
    _SEARCH_REPO_CODE_TOOL,
    _SUBMIT_FILE_REVIEW_TOOL,
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


async def _run_file_review_loop(
    file: ChangedFile,
    diff_snippet: str,
    system: str,
    tool_executors: dict[str, Any],
    anthropic_client: AsyncAnthropic,
) -> tuple[list[ReviewComment], Literal["low", "medium", "high", "critical"]]:
    """Focused mini-loop reviewing a single file; returns comments and a per-file risk level."""
    user_prompt = (
        f"Review this file: {file.filename}\n"
        f"Status: {file.status} (+{file.additions} -{file.deletions})\n\n"
        f"Diff:\n{diff_snippet}\n\n"
        f"Fetch file content with line ranges if you need more context. "
        f"When done, call submit_file_review with your findings."
    )
    messages: list[Any] = [{"role": "user", "content": user_prompt}]

    for iteration in range(_FILE_REVIEW_MAX_ITERATIONS):
        response = await anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            tools=_FILE_REVIEW_TOOLS,
            messages=cast("list[MessageParam]", messages),
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"File review truncated for {file.filename} at iteration {iteration}"
            )
        if response.stop_reason == "end_turn":
            raise RuntimeError(
                f"File review agent stopped without submitting for {file.filename} "
                f"(iteration {iteration})"
            )

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_block = cast(ToolUseBlock, block)
            if tool_block.name == "submit_file_review":
                raw_comments = cast("list[Any]", tool_block.input.get("comments", []))
                comments = [ReviewComment.model_validate(c) for c in raw_comments]
                risk: Literal["low", "medium", "high", "critical"] = cast(
                    "Literal['low', 'medium', 'high', 'critical']",
                    tool_block.input.get("risk_level", "low"),
                )
                return comments, risk
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
                f"No tool calls in file review at iteration {iteration} "
                f"for {file.filename}, stop_reason={response.stop_reason}"
            )

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(
        f"File review exceeded {_FILE_REVIEW_MAX_ITERATIONS} iterations for {file.filename}"
    )


async def _aggregate_file_reviews(
    all_comments: list[ReviewComment],
    all_risk_levels: list[Literal["low", "medium", "high", "critical"]],
    deps: ReviewDeps,
    system: str,
    anthropic_client: AsyncAnthropic,
) -> PRReview:
    """Single-turn forced call that synthesises per-file results into a full PRReview."""
    overall_risk = max(
        all_risk_levels, key=lambda r: _RISK_ORDER.get(r, 0), default="low"
    )
    comments_json = json.dumps([c.model_dump() for c in all_comments], indent=2)
    user_prompt = (
        f"PR #{deps.pr.number}: {deps.pr.title}\n"
        f"Author: {deps.pr.user.login} | Repo: {deps.repo.full_name}\n"
        f"Triage: {deps.triage.risk_level} risk, tags={deps.triage.tags}\n\n"
        f"Per-file review is complete. Collected comments:\n{comments_json}\n\n"
        f"Synthesise these into a final PR review. Overall risk is '{overall_risk}'. "
        f"Provide a summary, architectural observations, learning points, and approve decision."
    )
    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=[
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[_SUBMIT_REVIEW_TOOL],
        tool_choice=cast("Any", {"type": "tool", "name": "submit_review"}),
        messages=cast("list[MessageParam]", [{"role": "user", "content": user_prompt}]),
    )
    for block in response.content:
        if block.type != "tool_use":
            continue
        tool_block = cast(ToolUseBlock, block)
        if tool_block.name == "submit_review":
            return PRReview.model_validate(tool_block.input)
    raise RuntimeError("Aggregation call did not return submit_review")


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

    if len(changed_files) >= _PARALLEL_THRESHOLD:
        logger.info(
            "Using parallel subagents for %d files (PR #%d)",
            len(changed_files),
            pr.number,
        )
        full_diff = await git_client.get_pr_diff(workspace, repo_slug, pr.number)
        diff_by_file = _split_diff_by_file(full_diff)

        semaphore = asyncio.Semaphore(_FILE_REVIEW_CONCURRENCY)

        async def _review_one_file(
            file: ChangedFile,
        ) -> tuple[list[ReviewComment], Literal["low", "medium", "high", "critical"]]:
            diff_snippet = diff_by_file.get(file.filename, "(diff not available)")
            async with semaphore:
                return await _run_file_review_loop(
                    file=file,
                    diff_snippet=diff_snippet,
                    system=system,
                    tool_executors=tool_executors,
                    anthropic_client=anthropic_client,
                )

        file_results = await asyncio.gather(
            *(_review_one_file(f) for f in changed_files)
        )

        all_comments: list[ReviewComment] = [
            c for comments, _ in file_results for c in comments
        ]
        all_risk_levels: list[Literal["low", "medium", "high", "critical"]] = [
            risk for _, risk in file_results
        ]
        return await _aggregate_file_reviews(
            all_comments=all_comments,
            all_risk_levels=all_risk_levels,
            deps=deps,
            system=system,
            anthropic_client=anthropic_client,
        )

    # Single-loop path for small PRs (< _PARALLEL_THRESHOLD files)
    single_loop_executors = {
        "fetch_pr_diff": _fetch_pr_diff,
        **tool_executors,
    }
    return await _run_review_loop(
        system=system,
        user_prompt=_format_review_prompt(deps),
        tool_executors=single_loop_executors,
        anthropic_client=anthropic_client,
    )
