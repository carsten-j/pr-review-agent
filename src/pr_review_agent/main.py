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

from pr_review_agent.models import GitHubPullRequest, GitHubRepo, GitHubWebhookPayload
from pr_review_agent.security import verify_webhook_signature

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
        description="Secret for verifying GitHub webhook signatures"
    )
    anthropic_api_key: str = Field(description="Anthropic API key for the triage agent")
    github_token: str = Field(description="GitHub token for fetching PR details")
    github_api_base: str = Field(
        default="https://api.github.com",
        description="Base URL for the GitHub API",
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

    pr = payload.pull_request
    logger.info(
        "New PR opened: #%d '%s' by %s in %s — %s",
        pr.number,
        pr.title,
        pr.user.login,
        payload.repository.full_name,
        pr.html_url,
    )

    settings = get_settings()
    asyncio.create_task(
        _run_triage_background(
            pr=pr,
            repo=payload.repository,
            github_token=settings.github_token,
            github_api_base=settings.github_api_base,
            reviewer_role=settings.reviewer_role,
        )
    )

    return {
        "status": "received",
        "pr_number": str(pr.number),
        "title": pr.title,
        "author": pr.user.login,
        "url": pr.html_url,
    }


async def _run_triage_background(
    pr: GitHubPullRequest,
    repo: GitHubRepo,
    github_token: str,
    github_api_base: str,
    reviewer_role: str = "senior-dev",
) -> None:
    """Background task: run triage, then review if needed."""
    from pr_review_agent.github_client import GitHubClient
    from pr_review_agent.review import post_review_comments, run_review
    from pr_review_agent.triage import run_triage

    git_client = GitHubClient(github_token, base_url=github_api_base)
    try:
        with logfire.span(
            "review PR {repo}#{pr_number}",
            repo=repo.full_name,
            pr_number=pr.number,
            pr_title=pr.title,
            pr_author=pr.user.login,
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
