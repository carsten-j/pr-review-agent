from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Annotated

import logfire
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Request
from pydantic import Field
from pydantic_settings import BaseSettings

from pr_review_agent.models import (
    BitbucketWebhookPayload,
    GitHubWebhookPayload,
    PullRequestInfo,
    RepoInfo,
)
from pr_review_agent.security import (
    verify_bitbucket_webhook_signature,
    verify_webhook_signature,
)

load_dotenv()

logfire.configure()
logfire.instrument_pydantic_ai()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    github_webhook_secret: str = Field(
        default="",
        description="Secret for verifying GitHub webhook signatures",
    )
    anthropic_api_key: str = Field(description="Anthropic API key for the triage agent")
    github_token: str = Field(
        default="",
        description="GitHub token for fetching PR details",
    )
    github_api_base: str = Field(
        default="https://api.github.com",
        description="Base URL for the GitHub API",
    )
    bitbucket_webhook_secret: str = Field(
        default="",
        description="Secret for verifying Bitbucket webhook signatures",
    )
    bitbucket_username: str = Field(
        default="",
        description="Bitbucket username for API authentication",
    )
    bitbucket_app_password: str = Field(
        default="",
        description="Bitbucket app password for API authentication",
    )
    bitbucket_api_base: str = Field(
        default="https://api.bitbucket.org/2.0",
        description="Base URL for the Bitbucket API",
    )
    reviewer_role: str = Field(
        default="senior-dev",
        description="Reviewer persona for the general review agent",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # ty: ignore[missing-argument]  # populated from env vars


app = FastAPI(title="PR Review Agent", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _github_payload_to_domain(
    payload: GitHubWebhookPayload,
) -> tuple[PullRequestInfo, RepoInfo]:
    """Convert a GitHub webhook payload to platform-agnostic domain objects."""
    gh_pr = payload.pull_request
    return (
        PullRequestInfo(
            number=gh_pr.number,
            title=gh_pr.title,
            body=gh_pr.body,
            author_login=gh_pr.user.login,
            html_url=gh_pr.html_url,
            head_branch=gh_pr.head.ref,
            head_sha=gh_pr.head.sha,
            base_branch=gh_pr.base.ref,
        ),
        RepoInfo(
            full_name=payload.repository.full_name,
            is_private=payload.repository.private,
        ),
    )


def _bitbucket_payload_to_domain(
    payload: BitbucketWebhookPayload,
) -> tuple[PullRequestInfo, RepoInfo]:
    """Convert a Bitbucket webhook payload to platform-agnostic domain objects."""
    bb_pr = payload.pullrequest
    return (
        PullRequestInfo(
            number=bb_pr.id,
            title=bb_pr.title,
            body=bb_pr.description,
            author_login=payload.actor.nickname,
            html_url=bb_pr.links.html["href"],
            head_branch=bb_pr.source.branch.name,
            head_sha=bb_pr.source.commit.hash,
            base_branch=bb_pr.destination.branch.name,
        ),
        RepoInfo(
            full_name=payload.repository.full_name,
            is_private=payload.repository.is_private,
        ),
    )


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    body: Annotated[bytes, Depends(verify_webhook_signature)],
    x_github_event: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    if x_github_event != "pull_request":
        logger.info("Ignoring event: %s", x_github_event)
        return {"status": "ignored", "reason": f"event type: {x_github_event}"}

    payload = GitHubWebhookPayload.model_validate_json(body)

    if payload.action != "opened":
        logger.info(
            "Ignoring PR action: %s for PR #%d",
            payload.action,
            payload.pull_request.number,
        )
        return {"status": "ignored", "reason": f"action: {payload.action}"}

    pr_info, repo_info = _github_payload_to_domain(payload)
    logger.info(
        "New PR opened: #%d '%s' by %s in %s — %s",
        pr_info.number,
        pr_info.title,
        pr_info.author_login,
        repo_info.full_name,
        pr_info.html_url,
    )

    settings = get_settings()
    asyncio.create_task(
        _run_triage_background(
            pr=pr_info,
            repo=repo_info,
            platform="github",
            reviewer_role=settings.reviewer_role,
        )
    )

    return {
        "status": "received",
        "pr_number": str(pr_info.number),
        "title": pr_info.title,
        "author": pr_info.author_login,
        "url": pr_info.html_url,
    }


@app.post("/webhook/bitbucket")
async def bitbucket_webhook(
    request: Request,
    body: Annotated[bytes, Depends(verify_bitbucket_webhook_signature)],
    x_event_key: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    if x_event_key != "pullrequest:created":
        logger.info("Ignoring Bitbucket event: %s", x_event_key)
        return {"status": "ignored", "reason": f"event: {x_event_key}"}

    payload = BitbucketWebhookPayload.model_validate_json(body)
    pr_info, repo_info = _bitbucket_payload_to_domain(payload)
    logger.info(
        "New Bitbucket PR opened: #%d '%s' by %s in %s — %s",
        pr_info.number,
        pr_info.title,
        pr_info.author_login,
        repo_info.full_name,
        pr_info.html_url,
    )

    settings = get_settings()
    asyncio.create_task(
        _run_triage_background(
            pr=pr_info,
            repo=repo_info,
            platform="bitbucket",
            reviewer_role=settings.reviewer_role,
        )
    )

    return {
        "status": "received",
        "pr_number": str(pr_info.number),
        "title": pr_info.title,
        "author": pr_info.author_login,
        "url": pr_info.html_url,
    }


async def _run_triage_background(
    pr: PullRequestInfo,
    repo: RepoInfo,
    platform: str,
    reviewer_role: str = "senior-dev",
) -> None:
    """Background task: run triage, then review if needed."""
    from pr_review_agent.review import post_review_comments, run_review
    from pr_review_agent.triage import run_triage

    settings = get_settings()

    if platform == "bitbucket":
        from pr_review_agent.bitbucket_client import BitbucketClient

        git_client = BitbucketClient(
            settings.bitbucket_username,
            settings.bitbucket_app_password,
            base_url=settings.bitbucket_api_base,
        )
    else:
        from pr_review_agent.github_client import GitHubClient

        git_client = GitHubClient(
            settings.github_token, base_url=settings.github_api_base
        )

    try:
        with logfire.span(
            "review PR {repo}#{pr_number}",
            repo=repo.full_name,
            pr_number=pr.number,
            pr_title=pr.title,
            pr_author=pr.author_login,
        ):
            triage_result = await run_triage(pr=pr, repo=repo, git_client=git_client)
            logger.info(
                "Triage result for PR #%d: should_review=%s priority=%s "
                "risk_level=%s tags=%s reason=%s",
                pr.number,
                triage_result.should_review,
                triage_result.priority,
                triage_result.risk_level,
                triage_result.tags,
                triage_result.reason,
            )

            if not triage_result.should_review:
                logger.info("PR #%d: triage says skip review", pr.number)
                return

            owner, repo_name = repo.full_name.split("/", 1)
            changed_files = await git_client.get_pr_changed_files(
                owner, repo_name, pr.number
            )

            review = await run_review(
                pr=pr,
                repo=repo,
                git_client=git_client,
                triage_result=triage_result,
                changed_files=changed_files,
                reviewer_role=reviewer_role,
            )
            logger.info(
                "Review for PR #%d: risk=%s approve=%s comments=%d "
                "architectural_observations=%d learning_points=%d",
                pr.number,
                review.risk_level,
                review.approve,
                len(review.comments),
                len(review.architectural_observations),
                len(review.learning_points),
            )
            for comment in review.comments:
                logger.info(
                    "  [%s] %s:%d — %s",
                    comment.severity,
                    comment.file_path,
                    comment.line_start,
                    comment.comment,
                )
            await post_review_comments(
                git_client=git_client,
                workspace=owner,
                repo_slug=repo_name,
                pr_id=pr.number,
                review=review,
            )
            logger.info("Posted review to PR #%d", pr.number)
    except Exception:
        logger.exception("Pipeline failed for PR #%d", pr.number)
    finally:
        await git_client.close()
