from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Request
from pydantic import Field
from pydantic_settings import BaseSettings

from pr_review_agent.models import GitHubPullRequest, GitHubRepo, GitHubWebhookPayload
from pr_review_agent.security import verify_webhook_signature

load_dotenv()

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
) -> None:
    """Background task: run triage and log the result."""
    from pr_review_agent.triage import run_triage

    try:
        result = await run_triage(pr=pr, repo=repo, github_token=github_token)
        logger.info(
            "Triage result for PR #%d: should_review=%s priority=%s "
            "risk_level=%s tags=%s reason=%s",
            pr.number,
            result.should_review,
            result.priority,
            result.risk_level,
            result.tags,
            result.reason,
        )
    except Exception:
        logger.exception("Triage failed for PR #%d", pr.number)
